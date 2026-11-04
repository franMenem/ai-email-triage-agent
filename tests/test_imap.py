"""
ImapInbox against an in-memory fake IMAP server that answers like imaplib does.
Covers the whole cycle: pending -> draft in the Drafts folder -> not pending ->
the client writes again -> pending again. No network.
"""

import imaplib
import time
from email.message import EmailMessage

from imap_client import ImapInbox, group_conversations, imap_date, ImapRecord
from mail_core import looks_automated

ME = "fran@icloud.com"


def make_email(msg_id, sender, subject, body, in_reply_to=None, extra=None):
    m = EmailMessage()
    m["Message-ID"] = msg_id
    m["From"] = sender
    m["To"] = ME
    m["Subject"] = subject
    m["Date"] = "Wed, 04 Nov 2026 10:00:00 -0300"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (extra or {}).items():
        m[k] = v
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    """Just enough of imaplib.IMAP4: list, select, uid SEARCH/FETCH, append."""

    def __init__(self, folders):
        self.folders = {name: [] for name in folders}  # name -> [(uid, date, raw)]
        self.selected = None

    def add(self, folder, raw, date):
        uid = len(self.folders[folder]) + 1
        self.folders[folder].append((uid, date, raw))

    def list(self):
        flags = {"Sent Messages": r"\HasNoChildren \Sent", "Drafts": r"\HasNoChildren \Drafts"}
        return "OK", [f'({flags.get(n, chr(92) + "HasNoChildren")}) "/" "{n}"'.encode() for n in self.folders]

    def select(self, name, readonly=False):
        self.selected = name.strip('"')
        return ("OK", [b"1"]) if self.selected in self.folders else ("NO", [b"no folder"])

    def uid(self, command, *args):
        messages = self.folders[self.selected]
        if command == "SEARCH":
            return "OK", [b" ".join(str(uid).encode() for uid, _, _ in messages)]
        wanted = {int(u) for u in args[0].split(",")}
        response = []
        for uid, date, raw in messages:
            if uid in wanted:
                internal = imaplib.Time2Internaldate(date).encode()
                body = raw.split(b"\n\n")[0] + b"\n\n" if b"HEADER.FIELDS" in args[1].encode() else raw
                response += [(f"{uid} (UID {uid} INTERNALDATE ".encode() + internal + b" BODY[] {1}", body), b")"]
        return "OK", response

    def append(self, folder, flags, date, raw):
        self.add(folder.strip('"'), raw, time.time())
        return "OK", [b"APPEND completed"]


def new_server():
    return FakeIMAP(["INBOX", "agente", "Sent Messages", "Drafts"])


def test_finds_sent_and_drafts_by_their_flags():
    inbox = ImapInbox(new_server(), ME, "agente")
    assert (inbox._sent, inbox._drafts) == ("Sent Messages", "Drafts")


def test_full_cycle_pending_draft_and_new_message():
    server = new_server()
    now = time.time()
    server.add("agente", make_email("<a1@x>", "Mark <mark@x.com>", "Report", "Can we meet Tuesday?"), now - 300)

    inbox = ImapInbox(server, ME, "agente")
    [ref] = inbox.list_pending()
    mail = inbox.read_email(ref)
    assert (mail.sender, mail.subject, mail.body) == ("mark@x.com", "Report", "Can we meet Tuesday?")

    inbox.create_draft(ref, "Tuesday works.")
    draft = server.folders["Drafts"][0][2]
    assert b"In-Reply-To: <a1@x>" in draft and b"Re: Report" in draft
    assert ImapInbox(server, ME, "agente").list_pending() == [], "Already drafted: nothing to do"

    # The client writes again before the draft was sent: the new email gets its own reply
    server.add(
        "agente",
        make_email("<a2@x>", "Mark <mark@x.com>", "Re: Report", "Or Thursday?", in_reply_to="<a1@x>"),
        time.time() + 60,
    )
    inbox = ImapInbox(server, ME, "agente")
    [ref2] = inbox.list_pending()
    assert ref2 == "agente|2"
    assert "Can we meet Tuesday?" in inbox.read_email(ref2).thread


def test_nothing_pending_after_you_replied():
    server = new_server()
    server.add("agente", make_email("<b1@x>", "Daniel <l@x.com>", "Hi", "Question"), time.time() - 600)
    server.add("Sent Messages", make_email("<b2@x>", ME, "Re: Hi", "Answer", in_reply_to="<b1@x>"), time.time())
    assert ImapInbox(server, ME, "agente").list_pending() == []


def test_all_mail_mode_watches_inbox_and_skips_newsletters():
    server = new_server()
    server.add("INBOX", make_email("<c1@x>", "Ana <ana@x.com>", "Hola", "¿Me ayudás?"), time.time())
    server.add(
        "INBOX",
        make_email("<c2@x>", "News <news@shop.com>", "Ofertas", "50% off",
                   extra={"List-Unsubscribe": "<mailto:u@shop.com>"}),
        time.time(),
    )
    assert ImapInbox(server, ME, "*").list_pending() == ["INBOX|1"]


def test_conversations_are_grouped_by_threading_headers():
    r1 = ImapRecord("a|1", "watched", 1, {"message-id": "<1@x>"})
    r2 = ImapRecord("s|1", "sent", 2, {"message-id": "<2@x>", "in-reply-to": "<1@x>"})
    r3 = ImapRecord("a|2", "watched", 3, {"message-id": "<3@x>", "references": "<1@x> <2@x>"})
    other = ImapRecord("a|3", "watched", 4, {"message-id": "<9@x>"})
    groups = sorted(group_conversations([r1, r2, r3, other]), key=len)
    assert [len(g) for g in groups] == [1, 3]


def test_imap_dates_use_english_month_names():
    from datetime import datetime

    assert imap_date(datetime(2026, 11, 4)) == "04-Nov-2026"


def test_automated_senders_in_spanish_and_english():
    assert looks_automated({"from": "Banco <noresponder@banco.com.ar>"})
    assert looks_automated({"from": "Portal <sin-respuesta@portal.com>"})
    assert looks_automated({"from": "Shop <hi@shop.com>", "list-unsubscribe": "<mailto:x>"})
    assert not looks_automated({"from": "Mark <mark@x.com>"})
