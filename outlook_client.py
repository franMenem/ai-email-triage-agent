"""
Outlook inbox: Outlook.com, Hotmail and Microsoft 365, through Microsoft Graph.

Permissions: Mail.ReadWrite (read emails, create drafts) and User.Read. Mail.Send
is NOT requested, so this app can't send email even if someone wanted it to:
in Microsoft Graph, sending is a separate permission.

Setup (one time):
1. In https://entra.microsoft.com -> App registrations -> New registration.
   Supported account types: "Accounts in any organizational directory and personal
   Microsoft accounts". No redirect URI needed.
2. Authentication -> "Allow public client flows" -> Yes.
3. API permissions -> Microsoft Graph -> Delegated: Mail.ReadWrite, User.Read.
4. Put the "Application (client) ID" in MS_CLIENT_ID and run:
       uv run python outlook_client.py --login
   It shows a code to enter at microsoft.com/devicelogin and saves ms_token.json.
   On a server, put the contents of that file in MS_TOKEN_CACHE.
5. In Outlook, create a rule that adds the category "agente" to your clients'
   emails (or set AGENT_FILTER="*" for every email of the Focused inbox).

Push notifications: Graph "subscriptions" call POST /outlook-webhook when an email
arrives. They last at most 7 days and are renewed by POST /renew-watch, like Gmail.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import msal

from mail_core import (
    ALL_MAIL,
    PENDING_MAX_DAYS,
    ThreadMessage,
    email_to_answer,
    format_thread,
    looks_automated,
    strip_quoted,
)
from models import Email

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "User.Read"]
AUTHORITY = "https://login.microsoftonline.com/common"  # personal and work accounts
TOKEN_PATH = Path(__file__).parent / "ms_token.json"
# Graph allows up to 10,080 minutes for mail subscriptions; renewed every day anyway
SUBSCRIPTION_DAYS = 6
# Bodies as plain text, so Claude doesn't get HTML
TEXT_BODY = {"Prefer": 'outlook.body-content-type="text"'}


def _msal_app():
    cache = msal.SerializableTokenCache()
    if saved := os.environ.get("MS_TOKEN_CACHE"):
        cache.deserialize(saved)
    elif TOKEN_PATH.exists():
        cache.deserialize(TOKEN_PATH.read_text())
    app = msal.PublicClientApplication(os.environ["MS_CLIENT_ID"], authority=AUTHORITY, token_cache=cache)
    return app, cache


def login():
    """Device-code login: works without a browser redirect, also over SSH."""
    app, cache = _msal_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "Login failed"))
    TOKEN_PATH.write_text(cache.serialize())
    print(f"Logged in. Saved {TOKEN_PATH.name} (on a server: its contents go in MS_TOKEN_CACHE)")


def access_token():
    app, _ = _msal_app()
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    if not result or "access_token" not in result:
        raise RuntimeError("No valid Microsoft login: run `python outlook_client.py --login`")
    return result["access_token"]


def to_thread_messages(messages, me):
    """Graph messages of one conversation -> ThreadMessage."""
    out = []
    for m in messages:
        # A draft's date is when it was last edited; a received/sent email's, when it arrived
        when = m["lastModifiedDateTime"] if m.get("isDraft") else m["receivedDateTime"]
        sender = (m.get("from") or {}).get("emailAddress", {}).get("address", "").lower()
        out.append(
            ThreadMessage(
                id=m["id"],
                date=datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp(),
                from_me=sender == me,
                is_draft=bool(m.get("isDraft")),
            )
        )
    return out


def graph_headers(message):
    """Graph's internetMessageHeaders -> dict with lowercase names (for looks_automated)."""
    headers = {h["name"].lower(): h["value"] for h in message.get("internetMessageHeaders", [])}
    sender = (message.get("from") or {}).get("emailAddress", {})
    headers.setdefault("from", f"{sender.get('name', '')} <{sender.get('address', '')}>")
    return headers


class OutlookInbox:
    def __init__(self, http: httpx.Client, filter_value: str):
        self._http = http
        self._filter = filter_value
        self._me = self._get("/me")
        self._me_address = (self._me.get("mail") or self._me.get("userPrincipalName", "")).lower()
        self._conversations = {}  # message id -> its conversation (filled by list_pending)

    @classmethod
    def connect(cls):
        http = httpx.Client(
            base_url=GRAPH, headers={"Authorization": f"Bearer {access_token()}"}, timeout=30
        )
        return cls(http, os.environ.get("AGENT_FILTER", "agente"))

    def _get(self, path, params=None, headers=None):
        response = self._http.get(path, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def _recent_ids(self):
        """Conversation IDs of the recent emails the agent handles."""
        since = (datetime.now(timezone.utc) - timedelta(days=PENDING_MAX_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if self._filter == ALL_MAIL:
            path = "/me/mailFolders/inbox/messages"
            query = f"receivedDateTime ge {since} and inferenceClassification eq 'focused'"
        else:
            path = "/me/messages"
            query = f"receivedDateTime ge {since} and categories/any(c:c eq '{self._filter}')"
        data = self._get(path, params={"$filter": query, "$select": "conversationId", "$top": "100"})
        return {m["conversationId"] for m in data.get("value", [])}

    def list_pending(self):
        """IDs of the emails that still need a reply (see mail_core.email_to_answer)."""
        pending = []
        for conversation_id in self._recent_ids():
            data = self._get(
                "/me/messages",
                params={
                    "$filter": f"conversationId eq '{conversation_id}'",
                    "$select": "id,receivedDateTime,lastModifiedDateTime,isDraft,from,subject",
                },
            )
            messages = data.get("value", [])
            for m in messages:
                self._conversations[m["id"]] = messages
            message_id = email_to_answer(to_thread_messages(messages, self._me_address))
            if not message_id:
                continue
            if self._filter == ALL_MAIL:
                full = self._get(f"/me/messages/{message_id}", params={"$select": "from,internetMessageHeaders"})
                if looks_automated(graph_headers(full)):
                    continue
            pending.append(message_id)
        return pending

    def _message(self, message_id):
        return self._get(
            f"/me/messages/{message_id}",
            params={"$select": "subject,from,receivedDateTime,uniqueBody,body,conversationId"},
            headers=TEXT_BODY,
        )

    def read_email(self, message_id):
        """The email and its conversation. uniqueBody is only the new text of each
        message (Outlook already removes the quoted part); body is the fallback."""
        message = self._message(message_id)
        conversation = self._conversations.get(message_id, [])
        earlier = sorted(
            (m for m in conversation if not m.get("isDraft") and m["id"] != message_id),
            key=lambda m: m["receivedDateTime"],
        )
        thread = []
        for m in earlier:
            full = self._message(m["id"])
            sender = full["from"]["emailAddress"]
            text = (full.get("uniqueBody") or full["body"])["content"]
            thread.append((f"{sender.get('name', '')} <{sender['address']}>", full["receivedDateTime"], text))
        body = (message.get("uniqueBody") or message["body"])["content"]
        return Email(
            message_id=message_id,
            sender=message["from"]["emailAddress"]["address"],
            subject=message.get("subject", ""),
            body=strip_quoted(body),
            thread=format_thread(thread),
        )

    def create_draft(self, message_id, text):
        """createReply leaves the draft in the same conversation, quoting the original."""
        response = self._http.post(f"/me/messages/{message_id}/createReply", json={"comment": text})
        response.raise_for_status()
        return response.json()

    def start_watch(self):
        """Creates or renews the Graph subscription that calls /outlook-webhook.

        Needs PUBLIC_URL (the server's https address) and WEBHOOK_TOKEN, which Graph
        sends back as clientState in every notification. Returns the expiry.
        """
        url = os.environ["PUBLIC_URL"].rstrip("/") + "/outlook-webhook"
        expires = (datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        existing = [s for s in self._get("/subscriptions").get("value", []) if s["notificationUrl"] == url]
        if existing:
            response = self._http.patch(
                f"/subscriptions/{existing[0]['id']}", json={"expirationDateTime": expires}
            )
        else:
            response = self._http.post(
                "/subscriptions",
                json={
                    "changeType": "created",
                    "notificationUrl": url,
                    "resource": "me/mailFolders('inbox')/messages",
                    "expirationDateTime": expires,
                    "clientState": os.environ["WEBHOOK_TOKEN"],
                },
            )
        response.raise_for_status()
        return datetime.fromisoformat(response.json()["expirationDateTime"].replace("Z", "+00:00"))


if __name__ == "__main__":
    if "--login" in sys.argv:
        login()
        sys.exit()
    inbox = OutlookInbox.connect()
    print("Connected as:", inbox._me_address)
    ids = inbox.list_pending()
    print("Emails that still need a reply:", ids)
    for message_id in ids:
        mail = inbox.read_email(message_id)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
