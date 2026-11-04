"""
Provider-independent mail logic, shared by Gmail, Outlook and IMAP.

Each provider turns its own data into these simple shapes (ThreadMessage, header
dicts, plain text), and the rules live here once: which email still needs a
reply, which emails are automated, how a thread is shown to Claude, how a reply
is built. Pure functions only: no network, easy to test.
"""

import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

# Only conversations with activity in the last N days: old threads are never answered
PENDING_MAX_DAYS = 7
# How much of the thread Claude sees: the last N earlier messages, each cut to M characters
THREAD_MAX_MESSAGES = 10
THREAD_MAX_CHARS = 2000
# Value of AGENT_FILTER that means "every incoming email" instead of a label/category/folder
ALL_MAIL = "*"


@dataclass(frozen=True)
class ThreadMessage:
    """What the pending rule needs to know about each message of a conversation."""

    id: str
    date: float  # seconds since 1970 (received, sent or last edited, for drafts)
    from_me: bool  # sent by the mailbox owner
    is_draft: bool


def has_newer_draft(messages: list[ThreadMessage], message_id: str) -> bool:
    """True if the conversation has a draft written after `message_id` arrived.

    That draft answers this email (or a later one), so there's nothing to do.
    An older draft doesn't count: if the client writes again before you send
    it, the new email still gets its own draft.
    """
    received = next(m.date for m in messages if m.id == message_id)
    return any(m.is_draft and m.date > received for m in messages)


def email_to_answer(messages: list[ThreadMessage]) -> str | None:
    """ID of the email in this conversation that still needs a reply, or None.

    That's the latest message (drafts don't count) when:
    - it wasn't sent by you: otherwise you already answered;
    - and there's no draft written after it: otherwise it's already drafted.
    Whether you opened it doesn't matter: reading an email isn't answering it.
    """
    conversation = [m for m in messages if not m.is_draft]
    if not conversation:
        return None
    last = max(conversation, key=lambda m: m.date)
    if last.from_me or has_newer_draft(messages, last.id):
        return None
    return last.id


# Senders that never read replies, in English and Spanish ("noresponder", "sin-respuesta"...)
_NO_REPLY = re.compile(
    r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?@|bounce"
    r"|no-?respon(der|da)|sin-?respuesta|notificacion(es)?@|alertas?@)",
    re.IGNORECASE,
)


def looks_automated(headers: dict[str, str]) -> bool:
    """True for newsletters, notifications and auto-replies (headers in lowercase).

    Only matters when the agent reads every incoming email (AGENT_FILTER="*"):
    those emails don't need a reply, and drafting them would only cost money.
    """
    _, address = parseaddr(headers.get("from", ""))
    return (
        "list-unsubscribe" in headers  # newsletters and marketing
        or headers.get("precedence", "").lower() in ("bulk", "list", "junk")
        or headers.get("auto-submitted", "no").lower() != "no"  # auto-replies, out of office
        or bool(_NO_REPLY.search(address))
    )


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


def html_to_text(html: str) -> str:
    parser = _TextFromHTML()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# "On Mon, 3 Nov 2026, Mark <k@x.com> wrote:" / "El lun, 3 nov 2026, Mark escribió:"
_QUOTE_HEADER = re.compile(r"^(On .+ wrote:|El .+ escribió:)\s*$", re.IGNORECASE)
# Outlook's quote separator: "From: Mark <k@x.com>" right after a line of underscores or alone
_OUTLOOK_QUOTE = re.compile(r"^(_{10,}|-{2,} ?Original Message ?-{2,}|From: .+@.+)$", re.IGNORECASE)


def strip_quoted(text: str) -> str:
    """Removes the quoted earlier messages a reply carries below the new text.

    The agent already gets the earlier messages separately (the thread), so the
    quote would only repeat them.
    """
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if _QUOTE_HEADER.match(stripped) or _OUTLOOK_QUOTE.match(stripped):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def format_thread(earlier: list[tuple[str, str, str]]) -> str:
    """Earlier messages as text for Claude. `earlier`: (from, date, body), oldest first."""
    blocks = [
        f"From: {sender}\nDate: {date}\n{strip_quoted(body)[:THREAD_MAX_CHARS]}"
        for sender, date, body in earlier[-THREAD_MAX_MESSAGES:]
    ]
    return "\n---\n".join(blocks)


def build_reply_message(original_headers: dict[str, str], text: str) -> EmailMessage:
    """The reply to an email (headers in lowercase), threaded with In-Reply-To/References."""
    h = original_headers
    subject = h.get("subject", "")
    reply = EmailMessage()
    reply["To"] = h.get("reply-to") or h.get("from", "")
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    # These headers make email clients show it as a reply in the same conversation
    if "message-id" in h:
        reply["In-Reply-To"] = h["message-id"]
        references = f"{h['references']} {h['message-id']}" if h.get("references") else h["message-id"]
        reply["References"] = references
    reply.set_content(text)
    return reply
