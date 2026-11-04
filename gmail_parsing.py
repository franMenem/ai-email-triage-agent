"""
Pure functions that turn Gmail API data into what the agent needs.

No network and no login here: they take the dicts the Gmail API returns and give
back text. That's why they're easy to test (tests/test_gmail_parsing.py) and
gmail_client.py only has to deal with the API calls.
"""

import base64
import re
from email.message import EmailMessage
from html.parser import HTMLParser

# How much of the thread Claude sees: the last N earlier messages, each cut to M characters
THREAD_MAX_MESSAGES = 10
THREAD_MAX_CHARS = 2000


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


def has_newer_draft(thread_messages, message_id):
    """True if the thread already has a draft written after `message_id` arrived.

    That draft answers this email (or a later one), so there's nothing to do.
    An older draft doesn't count: if the client writes again before you send
    it, the new email still gets its own draft.
    """
    received = next(int(m["internalDate"]) for m in thread_messages if m["id"] == message_id)
    return any(
        "DRAFT" in m.get("labelIds", []) and int(m["internalDate"]) > received
        for m in thread_messages
    )


def build_reply(original_headers, text):
    """The reply as a raw (base64url) email, ready for the Gmail drafts API."""
    h = original_headers
    subject = h.get("subject", "")
    reply = EmailMessage()
    reply["To"] = h.get("reply-to") or h.get("from", "")
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    # These two headers make Gmail (and other clients) show it as a reply in the thread
    if "message-id" in h:
        reply["In-Reply-To"] = h["message-id"]
        reply["References"] = h["message-id"]
    reply.set_content(text)
    return base64.urlsafe_b64encode(reply.as_bytes()).decode()
