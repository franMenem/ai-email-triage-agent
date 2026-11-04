"""
Gmail client: read the emails with the agent label and leave drafts.

`connect()` is Google's standard login (OAuth). The first time it opens the
browser to grant access; then it saves token.json and doesn't ask again.
credentials.json and token.json are in .gitignore.

Permissions (SCOPES): read emails and create drafts. This file NEVER has a
function that sends email: a person reviews every draft and sends it.

Run it with: uv run python gmail_client.py   (doesn't call the Claude API)
"""

import base64
from email.message import EmailMessage
from email.utils import parseaddr
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

BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"


def connect():
    """Returns a Gmail API client authenticated with your account."""
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


def list_pending(service):
    """IDs of the unread emails with the agent label."""
    response = (
        service.users()
        .messages()
        .list(userId="me", q=f"label:{AGENT_LABEL} is:unread")
        .execute()
    )
    return [message["id"] for message in response.get("messages", [])]


def extract_text(payload):
    """Returns the plain-text body of a Gmail message.

    Gmail sends the body in base64 (URL variant) and, if the email has HTML
    or attachments, split into nested "parts". Finds the first text/plain part.
    """
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = extract_text(part)
        if text:
            return text
    return ""


def get_headers(message):
    """Turns the header list [{"name": ..., "value": ...}] into a dict."""
    return {h["name"].lower(): h["value"] for h in message["payload"]["headers"]}


def read_email(service, message_id):
    """Fetches a Gmail message and turns it into the `Email` the agent uses."""
    message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = get_headers(message)
    _, address = parseaddr(h.get("from", ""))  # "Mark <k@x.com>" -> "k@x.com"
    return Email(
        message_id=message_id,
        sender=address,
        subject=h.get("subject", ""),
        body=extract_text(message["payload"]),
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


if __name__ == "__main__":
    service = connect()
    profile = service.users().getProfile(userId="me").execute()
    print("Connected as:", profile["emailAddress"])

    ids = list_pending(service)
    print(f"Pending emails with the '{AGENT_LABEL}' label:", ids)
    for message_id in ids:
        mail = read_email(service, message_id)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
