"""
Pure functions that turn Gmail API data into the shapes mail_core.py works with.

No network and no login here: they take the dicts the Gmail API returns and give
back text or ThreadMessage records. The provider-independent rules (which email
needs a reply, HTML to text, quotes, reply building) live in mail_core.py.
"""

import base64

from mail_core import (
    ThreadMessage,
    build_reply_message,
    format_thread as format_thread_text,
    html_to_text,
)
from mail_core import email_to_answer as _email_to_answer
from mail_core import has_newer_draft as _has_newer_draft


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


def get_headers(message):
    """Turns the header list [{"name": ..., "value": ...}] into a dict (lowercase names)."""
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def to_thread_messages(messages):
    """Gmail thread messages -> ThreadMessage (labels say who sent it and what's a draft)."""
    return [
        ThreadMessage(
            id=m["id"],
            date=int(m["internalDate"]) / 1000,
            from_me="SENT" in m.get("labelIds", []),
            is_draft="DRAFT" in m.get("labelIds", []),
        )
        for m in messages
    ]


def has_newer_draft(thread_messages, message_id):
    return _has_newer_draft(to_thread_messages(thread_messages), message_id)


def email_to_answer(thread_messages):
    """ID of the email in this Gmail thread that still needs a reply, or None."""
    return _email_to_answer(to_thread_messages(thread_messages))


def format_thread(messages, current_id):
    """Earlier messages of the thread (oldest first), as text for Claude."""
    # Drafts also show up as thread messages: they're not part of the conversation
    earlier = [
        m for m in messages if m["id"] != current_id and "DRAFT" not in m.get("labelIds", [])
    ]
    entries = []
    for m in earlier:
        h = get_headers(m)
        entries.append((h.get("from", ""), h.get("date", ""), extract_text(m["payload"])))
    return format_thread_text(entries)


def build_reply(original_headers, text):
    """The reply as a raw (base64url) email, ready for the Gmail drafts API."""
    raw = build_reply_message(original_headers, text).as_bytes()
    return base64.urlsafe_b64encode(raw).decode()
