"""
Gmail client: the only file that talks to the Gmail API.

`connect()` is Google's standard login (OAuth). The first time it opens the
browser to grant access; then it saves token.json and doesn't ask again.
credentials.json and token.json are in .gitignore. On a server, the token
comes from the GMAIL_TOKEN_JSON environment variable instead.

`GmailInbox` wraps the API with the few operations the agent needs. The parsing
of what the API returns lives in gmail_parsing.py.

Permissions (SCOPES): read emails and create drafts. This file NEVER has a
method that sends email: a person reviews every draft and sends it.

Run it with: uv run python gmail_client.py           (lists pending emails)
             uv run python gmail_client.py --watch   (starts or renews the Gmail watch)
"""

import json
import os
import sys
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_parsing import (
    build_reply,
    extract_text,
    format_thread,
    get_headers,
    has_newer_draft,
    strip_quoted,
)
from models import Email

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
# Gmail label that a filter puts on the emails the agent should handle
AGENT_LABEL = "agente"
# Pub/Sub topic where Gmail publishes a notification when a labeled email arrives
PUBSUB_TOPIC = "projects/ai-email-triage-agent-513617/topics/gmail-agent"

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


class GmailInbox:
    """The inbox operations the agent needs, on top of a Gmail API client."""

    def __init__(self, service):
        self._messages = service.users().messages()
        self._threads = service.users().threads()
        self._service = service

    @classmethod
    def connect(cls):
        return cls(connect())

    def list_pending(self):
        """IDs of the unread emails with the agent label that don't have a draft yet.

        This is what makes the agent idempotent without keeping any state: an email
        is skipped once its thread has a draft newer than it. Delete the draft and
        the next run writes a new one. If the Claude call fails, no draft is
        created, so it's retried.
        """
        response = self._messages.list(userId="me", q=f"label:{AGENT_LABEL} is:unread").execute()
        pending = []
        for m in response.get("messages", []):
            thread = self._threads.get(userId="me", id=m["threadId"], format="minimal").execute()
            if not has_newer_draft(thread["messages"], m["id"]):
                pending.append(m["id"])
        return pending

    def read_email(self, message_id):
        """Fetches a Gmail message, with its thread, as the `Email` the agent uses."""
        message = self._messages.get(userId="me", id=message_id, format="full").execute()
        thread = self._threads.get(userId="me", id=message["threadId"], format="full").execute()
        h = get_headers(message)
        _, address = parseaddr(h.get("from", ""))  # "Mark <k@x.com>" -> "k@x.com"
        return Email(
            message_id=message_id,
            sender=address,
            subject=h.get("subject", ""),
            body=strip_quoted(extract_text(message["payload"])),
            thread=format_thread(thread["messages"], message_id),
        )

    def create_draft(self, message_id, text):
        """Saves `text` as a draft reply, inside the original email's thread."""
        original = self._messages.get(userId="me", id=message_id, format="metadata").execute()
        raw = build_reply(get_headers(original), text)
        body = {"message": {"raw": raw, "threadId": original["threadId"]}}
        return self._service.users().drafts().create(userId="me", body=body).execute()

    def label_id(self, name):
        """Gmail APIs take label IDs (e.g. "Label_12"), not names: finds the ID of `name`."""
        labels = self._service.users().labels().list(userId="me").execute()["labels"]
        for label in labels:
            if label["name"] == name:
                return label["id"]
        raise ValueError(f"There is no Gmail label called {name!r}")

    def start_watch(self):
        """Asks Gmail to publish to PUBSUB_TOPIC whenever an email gets the agent label.

        A watch lasts 7 days: it must be renewed before `expiration` (calling this
        again renews it). Returns {"historyId": ..., "expiration": <ms since 1970>}.
        """
        body = {
            "topicName": PUBSUB_TOPIC,
            "labelIds": [self.label_id(AGENT_LABEL)],
            "labelFilterBehavior": "include",
        }
        return self._service.users().watch(userId="me", body=body).execute()


if __name__ == "__main__":
    inbox = GmailInbox.connect()
    if "--watch" in sys.argv:
        response = inbox.start_watch()
        expires = datetime.fromtimestamp(int(response["expiration"]) / 1000)
        print(f"Watch active. historyId={response['historyId']} | expires {expires:%Y-%m-%d %H:%M}")
        sys.exit()

    ids = inbox.list_pending()
    print(f"Pending emails with the '{AGENT_LABEL}' label and no draft yet:", ids)
    for message_id in ids:
        mail = inbox.read_email(message_id)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
        if mail.thread:
            print(f"  Thread: {mail.thread[:200]!r}")
