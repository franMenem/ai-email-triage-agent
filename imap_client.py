"""
IMAP inbox: iCloud, Yahoo, AOL, Zoho, GMX, Mail.com, Proton (via Bridge) and any
provider that speaks IMAP, the standard most mailboxes support.

Configuration (environment variables):
    IMAP_PRESET    icloud | yahoo | aol | zoho | gmx | mailcom | proton   (or set IMAP_HOST)
    IMAP_HOST      server, e.g. imap.mail.me.com (overrides the preset)
    IMAP_PORT      993 by default (SSL); Proton Bridge uses 1143 with STARTTLS
    IMAP_STARTTLS  "true" for servers that start in plain text (Proton Bridge)
    IMAP_USER      the full email address
    IMAP_PASSWORD  an APP PASSWORD, not your normal password (iCloud, Yahoo, AOL,
                   Zoho and GMX require one when 2FA is on)
    AGENT_FILTER   folder the agent watches ("agente" by default; create it and a
                   rule that moves your clients' emails there), or "*" for INBOX

IMAP has no conversation IDs, so conversations are rebuilt from the Message-ID,
In-Reply-To and References headers, which every email client fills in. Drafts are
saved in the Drafts folder with those headers, so they show up as replies.

IMAP has no push notifications: run `process_inbox.py --every 5`, or call
POST /process on the server from a scheduler (see the README).

Like the other inboxes, there is no method that sends email.
"""

import email
import imaplib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from email.utils import formatdate, make_msgid, parseaddr

from mail_core import (
    ALL_MAIL,
    PENDING_MAX_DAYS,
    ThreadMessage,
    build_reply_message,
    email_to_answer,
    format_thread,
    html_to_text,
    looks_automated,
    strip_quoted,
)
from models import Email

# host, port, STARTTLS, and the folder names to try if the server doesn't mark them
PRESETS = {
    "icloud": ("imap.mail.me.com", 993, False),
    "yahoo": ("imap.mail.yahoo.com", 993, False),
    "aol": ("imap.aol.com", 993, False),
    "zoho": ("imap.zoho.com", 993, False),
    "gmx": ("imap.gmx.com", 993, False),
    "mailcom": ("imap.mail.com", 993, False),
    "proton": ("127.0.0.1", 1143, True),  # Proton Mail Bridge running on your computer
}
SENT_NAMES = ["Sent", "Sent Messages", "Sent Items", "Sent Mail", "Enviados", "Gesendet"]
DRAFT_NAMES = ["Drafts", "Draft", "Borradores", "Entwürfe"]

HEADER_FIELDS = (
    "MESSAGE-ID IN-REPLY-TO REFERENCES FROM TO DATE SUBJECT REPLY-TO "
    "LIST-UNSUBSCRIBE PRECEDENCE AUTO-SUBMITTED"
)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


@dataclass
class ImapRecord:
    """One message found while scanning: where it is, its role and its headers."""

    ref: str  # "folder|uid": the ID the rest of the agent uses
    role: str  # "watched", "sent" or "draft"
    date: float
    headers: dict[str, str] = field(default_factory=dict)

    def ids(self):
        """Message-IDs that tie this message to its conversation."""
        found = re.findall(
            r"<[^>]+>",
            " ".join(self.headers.get(k, "") for k in ("message-id", "in-reply-to", "references")),
        )
        return set(found)


def group_conversations(records):
    """Groups messages that share any Message-ID (union-find over the threading headers)."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in records:
        ids = list(r.ids()) or [f"no-id:{r.ref}"]
        for other in ids[1:]:
            parent[find(other)] = find(ids[0])
    groups = {}
    for r in records:
        root = find(next(iter(r.ids()), f"no-id:{r.ref}"))
        groups.setdefault(root, []).append(r)
    return list(groups.values())


def imap_date(when: datetime) -> str:
    """IMAP's SINCE format, e.g. 04-Nov-2026 (month names in English, whatever the locale)."""
    return f"{when.day:02d}-{_MONTHS[when.month - 1]}-{when.year}"


def parse_fetch(response):
    """imaplib FETCH response -> [(uid, internal date in seconds, raw bytes)]."""
    out = []
    for item in response:
        if not isinstance(item, tuple):
            continue
        meta, data = item
        uid = int(re.search(rb"UID (\d+)", meta).group(1))
        internal = imaplib.Internaldate2tuple(meta)
        out.append((uid, time.mktime(internal) if internal else time.time(), data))
    return out


def body_text(raw: bytes) -> str:
    """Plain text of a full email (text/plain preferred, HTML converted otherwise)."""
    msg = email.message_from_bytes(raw, policy=default_policy)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    return content if part.get_content_type() == "text/plain" else html_to_text(content)


class ImapInbox:
    def __init__(self, connection, user, watched_folder):
        self._con = connection
        self._user = user.lower()
        self._watched = "INBOX" if watched_folder == ALL_MAIL else watched_folder
        self._all_mail = watched_folder == ALL_MAIL
        self._sent, self._drafts = self._special_folders()
        self._records = {}  # ref -> ImapRecord, filled by list_pending
        self._groups = {}  # ref -> its conversation

    @classmethod
    def connect(cls):
        preset = PRESETS.get(os.environ.get("IMAP_PRESET", ""), (None, 993, False))
        host = os.environ.get("IMAP_HOST") or preset[0]
        port = int(os.environ.get("IMAP_PORT") or preset[1])
        starttls = os.environ.get("IMAP_STARTTLS", str(preset[2])).lower() == "true"
        if not host:
            raise ValueError("Set IMAP_PRESET or IMAP_HOST")
        if starttls:
            con = imaplib.IMAP4(host, port)
            con.starttls()
        else:
            con = imaplib.IMAP4_SSL(host, port)
        con.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
        return cls(con, os.environ["IMAP_USER"], os.environ.get("AGENT_FILTER", "agente"))

    def _special_folders(self):
        """Finds the Sent and Drafts folders: by their standard flags (\\Sent, \\Drafts)
        when the server marks them, otherwise by the usual names."""
        _, lines = self._con.list()
        names, by_flag = [], {}
        for line in lines:
            match = _LIST_LINE.match(line or b"")
            if not match:
                continue
            name = match["name"].decode().strip('"')
            names.append(name)
            for flag in (b"\\Sent", b"\\Drafts"):
                if flag in match["flags"]:
                    by_flag[flag] = name

        def pick(flag, candidates):
            return by_flag.get(flag) or next((n for n in candidates if n in names), candidates[0])

        return pick(b"\\Sent", SENT_NAMES), pick(b"\\Drafts", DRAFT_NAMES)

    def _scan(self, folder, role):
        """Headers of the recent messages of a folder. PEEK: nothing gets marked as read."""
        status, _ = self._con.select(f'"{folder}"', readonly=True)
        if status != "OK":
            return []
        since = imap_date(datetime.now() - timedelta(days=PENDING_MAX_DAYS))
        _, data = self._con.uid("SEARCH", None, f"SINCE {since}")
        uids = data[0].split() if data and data[0] else []
        if not uids:
            return []
        query = f"(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])"
        _, response = self._con.uid("FETCH", b",".join(uids).decode(), query)
        records = []
        for uid, date, raw in parse_fetch(response):
            parsed = BytesHeaderParser(policy=default_policy).parsebytes(raw)
            headers = {k.lower(): str(v) for k, v in parsed.items()}
            records.append(ImapRecord(f"{folder}|{uid}", role, date, headers))
        return records

    def _to_thread_message(self, r):
        _, sender = parseaddr(r.headers.get("from", ""))
        return ThreadMessage(
            id=r.ref,
            date=r.date,
            from_me=r.role == "sent" or sender.lower() == self._user,
            is_draft=r.role == "draft",
        )

    def list_pending(self):
        """Refs of the watched emails that still need a reply (see mail_core.email_to_answer)."""
        records = (
            self._scan(self._watched, "watched")
            + self._scan(self._sent, "sent")
            + self._scan(self._drafts, "draft")
        )
        self._records = {r.ref: r for r in records}
        pending = []
        for group in group_conversations(records):
            for r in group:
                self._groups[r.ref] = group
            if not any(r.role == "watched" for r in group):
                continue
            ref = email_to_answer([self._to_thread_message(r) for r in group])
            if ref and not (self._all_mail and looks_automated(self._records[ref].headers)):
                pending.append(ref)
        return pending

    def _fetch_full(self, ref):
        folder, uid = ref.rsplit("|", 1)
        self._con.select(f'"{folder}"', readonly=True)
        _, response = self._con.uid("FETCH", uid, "(UID INTERNALDATE BODY.PEEK[])")
        return parse_fetch(response)[0][2]

    def read_email(self, message_id):
        if message_id not in self._records:
            self.list_pending()
        record = self._records[message_id]
        earlier = sorted(
            (r for r in self._groups[message_id] if r.role != "draft" and r.ref != message_id),
            key=lambda r: r.date,
        )
        thread = [
            (r.headers.get("from", ""), r.headers.get("date", ""), body_text(self._fetch_full(r.ref)))
            for r in earlier
        ]
        _, sender = parseaddr(record.headers.get("from", ""))
        return Email(
            message_id=message_id,
            sender=sender,
            subject=record.headers.get("subject", ""),
            body=strip_quoted(body_text(self._fetch_full(message_id))),
            thread=format_thread(thread),
        )

    def create_draft(self, message_id, text):
        """Saves the reply in the Drafts folder, threaded with the original email."""
        if message_id not in self._records:
            self.list_pending()
        reply = build_reply_message(self._records[message_id].headers, text)
        reply["From"] = self._user
        reply["Date"] = formatdate(localtime=True)
        reply["Message-ID"] = make_msgid()
        now = imaplib.Time2Internaldate(time.time())
        status, detail = self._con.append(f'"{self._drafts}"', r"(\Draft)", now, reply.as_bytes())
        if status != "OK":
            raise RuntimeError(f"Could not save the draft in {self._drafts}: {detail}")


if __name__ == "__main__":
    inbox = ImapInbox.connect()
    print(f"Watching '{inbox._watched}' | Sent: '{inbox._sent}' | Drafts: '{inbox._drafts}'")
    ids = inbox.list_pending()
    print("Emails that still need a reply:", ids)
    for ref in ids:
        mail = inbox.read_email(ref)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
