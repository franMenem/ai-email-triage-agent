"""
Gmail client: read the emails with the agent label and leave drafts.

`connect()` is Google's standard login (OAuth). The first time it opens the
browser to grant access; then it saves token.json and doesn't ask again.
credentials.json and token.json are in .gitignore. On a server, the token
comes from the GMAIL_TOKEN_JSON environment variable instead.

Permissions (SCOPES): read emails and create drafts. This file NEVER has a
function that sends email: a person reviews every draft and sends it.

Run it with: uv run python gmail_client.py   (doesn't call the Claude API)
"""

import base64
import json
import os
import re
import sys
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from models import Email

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
# Gmail label that a filter puts on the emails the agent should handle
AGENT_LABEL = "agente"
# Pub/Sub topic where Gmail publishes a notification when a labeled email arrives
PUBSUB_TOPIC = "projects/ai-email-triage-agent-513617/topics/gmail-agent"
# How much of the thread Claude sees: the last N earlier messages, each cut to M characters
THREAD_MAX_MESSAGES = 10
THREAD_MAX_CHARS = 2000

BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"


def connect():
    """Returns a Gmail API client authenticated with your account."""
    # On a server there is no browser and no token.json: the token comes from the
    # GMAIL_TOKEN_JSON environment variable (the contents of your local token.json).
    # The refresh token inside it renews the access token in memory.
    if token_json := os.environ.get("GMAIL_TOKEN_JSON"):
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
        return build("gmail", "v1", credentials=creds)

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def threads_with_drafts(service):
    """IDs of the threads that already have a draft (made by the agent or by you)."""
    response = service.users().drafts().list(userId="me", maxResults=500).execute()
    return {d["message"]["threadId"] for d in response.get("drafts", [])}


def list_pending(service):
    """IDs of the unread emails with the agent label whose thread has no draft yet.

    This is what makes the agent idempotent without keeping any state: once a
    thread has a draft, it's skipped. Delete the draft and the next run writes
    a new one. If the Claude call fails, no draft is created, so it's retried.
    """
    response = (
        service.users()
        .messages()
        .list(userId="me", q=f"label:{AGENT_LABEL} is:unread")
        .execute()
    )
    with_drafts = threads_with_drafts(service)
    return [m["id"] for m in response.get("messages", []) if m["threadId"] not in with_drafts]


class _TextFromHTML(HTMLParser):
    """Keeps the visible text of an HTML email (skips <script> and <style>)."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextFromHTML()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _decode(part):
    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")


def _find_part(payload, mime_type):
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return payload
    for part in payload.get("parts", []):
        found = _find_part(part, mime_type)
        if found:
            return found
    return None


def extract_text(payload):
    """Returns the body of a Gmail message as plain text.

    Gmail sends the body in base64 (URL variant) and, if the email has HTML
    or attachments, split into nested "parts". Prefers the text/plain part;
    if the email only has HTML, converts it to text.
    """
    plain = _find_part(payload, "text/plain")
    if plain:
        return _decode(plain)
    html = _find_part(payload, "text/html")
    return html_to_text(_decode(html)) if html else ""


# "On Mon, 3 Nov 2026, Mark <k@x.com> wrote:" / "El lun, 3 nov 2026, Mark escribió:"
_QUOTE_HEADER = re.compile(r"^(On .+ wrote:|El .+ escribió:)\s*$", re.IGNORECASE)


def strip_quoted(text):
    """Removes the quoted earlier messages a reply carries below the new text.

    The agent already gets the earlier messages separately (the thread), so the
    quote would only repeat them.
    """
    kept = []
    for line in text.splitlines():
        if _QUOTE_HEADER.match(line.strip()):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def get_headers(message):
    """Turns the header list [{"name": ..., "value": ...}] into a dict."""
    return {h["name"].lower(): h["value"] for h in message["payload"]["headers"]}


def format_thread(messages, current_id):
    """Earlier messages of the thread (oldest first), as text for Claude."""
    # Drafts also show up as thread messages: they're not part of the conversation
    earlier = [
        m for m in messages if m["id"] != current_id and "DRAFT" not in m.get("labelIds", [])
    ]
    earlier = earlier[-THREAD_MAX_MESSAGES:]
    blocks = []
    for m in earlier:
        h = get_headers(m)
        body = strip_quoted(extract_text(m["payload"]))[:THREAD_MAX_CHARS]
        blocks.append(f"From: {h.get('from', '')}\nDate: {h.get('date', '')}\n{body}")
    return "\n---\n".join(blocks)


def read_email(service, message_id):
    """Fetches a Gmail message, with its thread, as the `Email` the agent uses."""
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    thread = service.users().threads().get(userId="me", id=message["threadId"], format="full").execute()
    h = get_headers(message)
    _, address = parseaddr(h.get("from", ""))  # "Mark <k@x.com>" -> "k@x.com"
    return Email(
        message_id=message_id,
        sender=address,
        subject=h.get("subject", ""),
        body=strip_quoted(extract_text(message["payload"])),
        thread=format_thread(thread["messages"], message_id),
    )


def create_draft(service, message_id, text):
    """Saves `text` as a draft reply, inside the original email's thread."""
    original = (
        service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
    )
    h = get_headers(original)
    subject = h.get("subject", "")

    reply = EmailMessage()
    reply["To"] = h.get("reply-to") or h.get("from", "")
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    # These two headers make Gmail (and other clients) show it as a reply in the thread
    if "message-id" in h:
        reply["In-Reply-To"] = h["message-id"]
        reply["References"] = h["message-id"]
    reply.set_content(text)

    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    return (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw, "threadId": original["threadId"]}})
        .execute()
    )


def label_id(service, name):
    """Gmail APIs take label IDs (e.g. "Label_12"), not names: finds the ID of `name`."""
    labels = service.users().labels().list(userId="me").execute()["labels"]
    for label in labels:
        if label["name"] == name:
            return label["id"]
    raise ValueError(f"There is no Gmail label called {name!r}")


def start_watch(service):
    """Asks Gmail to publish to PUBSUB_TOPIC whenever an email gets the agent label.

    A watch lasts 7 days: it must be renewed before `expiration` (calling this again
    renews it). Returns {"historyId": ..., "expiration": <milliseconds since 1970>}.
    """
    body = {
        "topicName": PUBSUB_TOPIC,
        "labelIds": [label_id(service, AGENT_LABEL)],
        "labelFilterBehavior": "include",
    }
    return service.users().watch(userId="me", body=body).execute()


if __name__ == "__main__":
    if "--watch" in sys.argv:
        # uv run python gmail_client.py --watch   -> starts (or renews) the Gmail watch
        response = start_watch(connect())
        expires = datetime.fromtimestamp(int(response["expiration"]) / 1000)
        print(f"Watch active. historyId={response['historyId']} | expires {expires:%Y-%m-%d %H:%M}")
        sys.exit()

    service = connect()
    profile = service.users().getProfile(userId="me").execute()
    print("Connected as:", profile["emailAddress"])

    ids = list_pending(service)
    print(f"Pending emails with the '{AGENT_LABEL}' label and no draft yet:", ids)
    for message_id in ids:
        mail = read_email(service, message_id)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
        if mail.thread:
            print(f"  Thread: {mail.thread[:200]!r}")
