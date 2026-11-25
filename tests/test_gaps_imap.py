"""Proposed tests: imap_client.py (in-memory fake IMAP, no network)."""

import imaplib
import re
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest

from imap_client import (
    ImapInbox,
    ImapRecord,
    body_text,
    group_conversations,
    imap_date,
    parse_fetch,
)

ME = "fran@icloud.com"
DAY = 86400


def make_email(msg_id, sender, subject, body, in_reply_to=None, extra=None, html=None):
    m = EmailMessage()
    if msg_id:
        m["Message-ID"] = msg_id
    m["From"] = sender
    m["To"] = ME
    if subject is not None:
        m["Subject"] = subject
    m["Date"] = "Wed, 04 Nov 2026 10:00:00 -0300"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (extra or {}).items():
        m[k] = v
    if body is not None:
        m.set_content(body)
        if html:
            m.add_alternative(html, subtype="html")
    elif html:
        m.set_content(html, subtype="html")
    return m.as_bytes()


class FakeIMAP:
    """Like tests/test_imap.py's fake, plus: SEARCH honours SINCE, sequence numbers differ
    from UIDs, configurable LIST flags, APPEND can fail, and it records what it was asked."""

    def __init__(self, folders, flags=None, append_status="OK"):
        self.folders = {name: [] for name in folders}
        self.flags = (
            flags
            if flags is not None
            else {"Sent Messages": r"\Sent", "Drafts": r"\Drafts"}
        )
        self.append_status = append_status
        self.selected = None
        self.searches, self.appends = [], []

    def add(self, folder, raw, date):
        uid = (
            100 + len(self.folders[folder]) + 1
        )  # UID != sequence number, like a real mailbox
        self.folders[folder].append((uid, date, raw))
        return f"{folder}|{uid}"

    def list(self):
        return "OK", [
            f'(\\HasNoChildren {self.flags.get(n, "")}) "/" "{n}"'.encode()
            for n in self.folders
        ]

    def select(self, name, readonly=False):
        self.selected = name.strip('"')
        return (
            ("OK", [b"1"]) if self.selected in self.folders else ("NO", [b"no folder"])
        )

    def uid(self, command, *args):
        messages = self.folders[
            self.selected
        ]  # KeyError if the client ignored select()'s "NO"
        if command == "SEARCH":
            self.searches.append(args[1])
            since = re.fullmatch(r"SINCE (\d\d-\w{3}-\d{4})", args[1])
            assert since, f"unexpected search criterion: {args[1]!r}"
            limit = time.mktime(time.strptime(since.group(1), "%d-%b-%Y"))
            return "OK", [
                b" ".join(
                    str(uid).encode() for uid, date, _ in messages if date >= limit
                )
            ]
        wanted = {int(u) for u in args[0].split(",")}
        response = []
        for seq, (uid, date, raw) in enumerate(messages, start=1):
            if uid in wanted:
                internal = imaplib.Time2Internaldate(date).encode()
                body = (
                    raw.split(b"\n\n")[0] + b"\n\n"
                    if "HEADER.FIELDS" in args[1]
                    else raw
                )
                response += [
                    (
                        f"{seq} (UID {uid} INTERNALDATE ".encode()
                        + internal
                        + b" BODY[] {1}",
                        body,
                    ),
                    b")",
                ]
        return "OK", response

    def append(self, folder, flags, date, raw):
        self.appends.append((folder.strip('"'), flags))
        if self.append_status != "OK":
            return self.append_status, [b"[OVERQUOTA] no space"]
        self.add(folder.strip('"'), raw, time.time())
        return "OK", [b"APPEND completed"]


def new_server(**kw):
    return FakeIMAP(["INBOX", "agente", "Sent Messages", "Drafts"], **kw)


# ---------- pure helpers ----------


def test_parse_fetch_takes_the_uid_not_the_sequence_number():
    meta = b'7 (UID 42 INTERNALDATE "04-Nov-2026 10:00:00 +0000" BODY[] {5}'
    [(uid, date, raw)] = parse_fetch([(meta, b"hello"), b")"])
    assert (uid, raw) == (42, b"hello")
    assert date == time.mktime(imaplib.Internaldate2tuple(meta))


def test_grouping_by_in_reply_to_alone():
    r1 = ImapRecord("a|1", "watched", 1, {"message-id": "<1@x>"})
    r2 = ImapRecord(
        "a|2", "watched", 2, {"message-id": "<2@x>", "in-reply-to": "<1@x>"}
    )
    assert [len(g) for g in group_conversations([r1, r2])] == [2]


def test_emails_without_message_id_are_separate_conversations():
    r1 = ImapRecord("a|1", "watched", 1, {"from": "a@x.com"})
    r2 = ImapRecord("a|2", "watched", 2, {"from": "b@x.com"})
    assert sorted(len(g) for g in group_conversations([r1, r2])) == [1, 1]


def test_body_text_html_only_is_converted():
    raw = make_email(
        "<h@x>",
        "a@x.com",
        "s",
        None,
        html="<html><style>p{color:red}</style><p>Hola</p><p>Chau</p></html>",
    )
    assert body_text(raw) == "Hola\nChau"


def test_body_text_prefers_plain_and_skips_attachments():
    m = EmailMessage()
    m["From"] = "a@x.com"
    m.set_content("Texto plano")
    m.add_alternative("<p>Version HTML</p>", subtype="html")
    m.add_attachment(
        b"%PDF-1.4", maintype="application", subtype="pdf", filename="a.pdf"
    )
    m.add_attachment("texto adjunto", filename="notas.txt")
    assert body_text(m.as_bytes()).strip() == "Texto plano"


def test_body_text_decodes_latin1_quoted_printable_and_base64():
    raw = (
        b"From: a@x.com\r\nContent-Type: text/plain; charset=iso-8859-1\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\nSe=F1or, =BFc=F3mo est=E1?\r\n"
    )
    assert body_text(raw).strip() == "Señor, ¿cómo está?"
    m = EmailMessage()
    m.set_content("Año nuevo ☃", cte="base64")
    assert body_text(m.as_bytes()).strip() == "Año nuevo ☃"


def test_body_text_of_an_email_with_only_an_attachment_is_empty():
    m = EmailMessage()
    m["From"] = "a@x.com"
    m.add_attachment(
        b"%PDF-1.4", maintype="application", subtype="pdf", filename="a.pdf"
    )
    assert body_text(m.as_bytes()) == ""


# ---------- special folders ----------


def test_sent_and_drafts_are_found_by_flag_even_with_unusual_names():
    server = FakeIMAP(
        ["INBOX", "Elementos enviados", "Mis borradores", "Sent", "Drafts"],
        flags={"Elementos enviados": r"\Sent", "Mis borradores": r"\Drafts"},
    )
    inbox = ImapInbox(server, ME, "agente")
    assert (inbox._sent, inbox._drafts) == ("Elementos enviados", "Mis borradores")


def test_sent_and_drafts_are_found_by_name_when_the_server_has_no_flags():
    server = FakeIMAP(["INBOX", "Enviados", "Borradores"], flags={})
    inbox = ImapInbox(server, ME, "agente")
    assert (inbox._sent, inbox._drafts) == ("Enviados", "Borradores")


# ---------- which emails are pending ----------


def test_only_the_last_7_days_are_searched():
    server = new_server()
    old = server.add(
        "agente",
        make_email("<old@x>", "Mark <mark@x.com>", "Old", "Hi"),
        time.time() - 10 * DAY,
    )
    new = server.add(
        "agente",
        make_email("<new@x>", "Ana <ana@x.com>", "New", "Hi"),
        time.time() - 2 * DAY,
    )
    assert ImapInbox(server, ME, "agente").list_pending() == [new] != [old]
    assert set(server.searches) == {
        f"SINCE {imap_date(datetime.now() - timedelta(days=7))}"
    }


def test_a_missing_watched_folder_means_nothing_pending():
    assert ImapInbox(new_server(), ME, "no-existe").list_pending() == []


def test_my_own_message_in_the_watched_folder_is_not_pending():
    # e.g. a rule filed my reply in the watched folder; the address comes in another case
    server = new_server()
    server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Hi", "Question"),
        time.time() - 600,
    )
    server.add(
        "agente",
        make_email(
            "<a2@x>", "Fran <FRAN@iCloud.com>", "Re: Hi", "Answer", in_reply_to="<a1@x>"
        ),
        time.time(),
    )
    assert ImapInbox(server, "Fran@icloud.com", "agente").list_pending() == []


def test_a_reply_in_sent_counts_as_mine_even_from_an_alias():
    server = new_server()
    server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Hi", "Question"),
        time.time() - 600,
    )
    server.add(
        "Sent Messages",
        make_email(
            "<a2@x>",
            "Ventas <ventas@miempresa.com>",
            "Re: Hi",
            "Answer",
            in_reply_to="<a1@x>",
        ),
        time.time(),
    )
    assert ImapInbox(server, ME, "agente").list_pending() == []


def test_client_reply_after_mine_is_pending_whatever_the_folder_order():
    # Records come watched-first, then sent: the latest must be picked by date
    server = new_server()
    server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Hi", "Question"),
        time.time() - 900,
    )
    server.add(
        "Sent Messages",
        make_email("<a2@x>", ME, "Re: Hi", "Answer", in_reply_to="<a1@x>"),
        time.time() - 600,
    )
    last = server.add(
        "agente",
        make_email(
            "<a3@x>",
            "Mark <mark@x.com>",
            "Re: Hi",
            "Thanks, and?",
            in_reply_to="<a2@x>",
        ),
        time.time() - 300,
    )
    assert ImapInbox(server, ME, "agente").list_pending() == [last]


def test_folder_mode_keeps_newsletters_and_all_mail_mode_skips_auto_replies():
    server = new_server()
    extra = {"List-Unsubscribe": "<mailto:u@shop.com>"}
    ref = server.add(
        "agente",
        make_email("<n1@x>", "News <news@shop.com>", "Ofertas", "50% off", extra=extra),
        time.time(),
    )
    assert ImapInbox(server, ME, "agente").list_pending() == [ref], (
        "the user chose this folder: no automated filter"
    )

    server.add(
        "INBOX",
        make_email(
            "<o1@x>",
            "Ana <ana@x.com>",
            "Re: Hola",
            "Estoy de vacaciones",
            extra={"Auto-Submitted": "auto-replied"},
        ),
        time.time(),
    )
    human = server.add(
        "INBOX",
        make_email("<o2@x>", "Ana <ana@x.com>", "Hola", "¿Me ayudás?"),
        time.time(),
    )
    assert ImapInbox(server, ME, "*").list_pending() == [human]


# ---------- read_email ----------


def long_thread(server):
    now = time.time()
    server.add(
        "agente",
        make_email("<t1@x>", "Mark <mark@x.com>", "Report", "First question"),
        now - 900,
    )
    server.add(
        "Sent Messages",
        make_email("<t2@x>", ME, "Re: Report", "My answer", in_reply_to="<t1@x>"),
        now - 800,
    )
    server.add(
        "agente",
        make_email(
            "<t3@x>",
            "Mark <mark@x.com>",
            "Re: Report",
            "Second question",
            in_reply_to="<t2@x>",
        ),
        now - 700,
    )
    server.add(
        "Drafts",
        make_email(
            "<d1@x>", ME, "Re: Report", "Unsent draft text", in_reply_to="<t3@x>"
        ),
        now - 600,
    )
    quoted = "Third question\n\nOn Wed, 4 Nov 2026, Fran wrote:\n> My answer"
    return server.add(
        "agente",
        make_email(
            "<t4@x>", "Mark <mark@x.com>", "Re: Report", quoted, in_reply_to="<t3@x>"
        ),
        now - 500,
    )


def test_read_email_thread_is_oldest_first_without_drafts_or_the_current_email():
    server = new_server()
    ref = long_thread(server)
    inbox = ImapInbox(server, ME, "agente")
    assert inbox.list_pending() == [ref]
    mail = inbox.read_email(ref)
    assert mail.body == "Third question"
    t = mail.thread
    assert t.index("First question") < t.index("My answer") < t.index("Second question")
    assert "Unsent draft text" not in t and "Third question" not in t
    assert t.startswith(
        "From: Mark <mark@x.com>\nDate: Wed, 04 Nov 2026 10:00:00 -0300\nFirst question"
    )


def test_read_email_and_create_draft_work_on_a_fresh_connection():
    # The webhook/CLI may get a ref from a previous run: the inbox must rescan by itself
    server = new_server()
    ref = server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Report", "Hi"),
        time.time() - 300,
    )
    assert ImapInbox(server, ME, "agente").read_email(ref).body == "Hi"
    ImapInbox(server, ME, "agente").create_draft(ref, "Hola")
    assert len(server.folders["Drafts"]) == 1


def test_read_email_without_subject_and_html_only():
    server = new_server()
    ref = server.add(
        "agente",
        make_email("<a1@x>", "mark@x.com", None, None, html="<p>Solo <b>HTML</b></p>"),
        time.time() - 300,
    )
    inbox = ImapInbox(server, ME, "agente")
    assert inbox.list_pending() == [ref]
    mail = inbox.read_email(ref)
    assert (mail.sender, mail.subject, mail.body, mail.thread) == (
        "mark@x.com",
        "",
        "Solo HTML",
        "",
    )


# ---------- create_draft ----------


def test_draft_has_the_draft_flag_and_the_headers_of_a_real_reply():
    server = new_server()
    ref = server.add(
        "agente",
        make_email(
            "<a1@x>",
            "Mark <mark@x.com>",
            "Re: Report",
            "Hi",
            extra={"Reply-To": "soporte@x.com"},
        ),
        time.time() - 300,
    )
    inbox = ImapInbox(server, ME, "agente")
    inbox.list_pending()
    inbox.create_draft(ref, "Tuesday works.")
    assert server.appends == [("Drafts", r"(\Draft)")]
    assert server.folders["Sent Messages"] == []
    import email as email_lib

    draft = email_lib.message_from_bytes(server.folders["Drafts"][0][2])
    assert (draft["From"], draft["To"], draft["Subject"]) == (
        ME,
        "soporte@x.com",
        "Re: Report",
    )
    assert draft["In-Reply-To"] == "<a1@x>" and draft["References"] == "<a1@x>"
    assert draft["Message-ID"] and draft["Date"]
    assert draft.get_payload().strip() == "Tuesday works."


def test_a_failed_append_raises_instead_of_pretending_the_draft_exists():
    server = new_server(append_status="NO")
    ref = server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Report", "Hi"),
        time.time() - 300,
    )
    inbox = ImapInbox(server, ME, "agente")
    inbox.list_pending()
    with pytest.raises(RuntimeError):
        inbox.create_draft(ref, "Hola")


def test_a_draft_written_from_an_alias_is_a_draft_not_a_client_email():
    server = new_server()
    server.add(
        "agente",
        make_email("<a1@x>", "Mark <mark@x.com>", "Hi", "Question"),
        time.time() - 600,
    )
    server.add(
        "Drafts",
        make_email(
            "<d1@x>",
            "Ventas <ventas@miempresa.com>",
            "Re: Hi",
            "Borrador",
            in_reply_to="<a1@x>",
        ),
        time.time() - 300,
    )
    assert ImapInbox(server, ME, "agente").list_pending() == []


def test_folder_names_with_a_pipe_still_work():
    server = FakeIMAP(["INBOX", "clientes|vip", "Sent Messages", "Drafts"])
    ref = server.add(
        "clientes|vip",
        make_email("<a1@x>", "Mark <mark@x.com>", "Hi", "Question"),
        time.time() - 600,
    )
    inbox = ImapInbox(server, ME, "clientes|vip")
    assert inbox.list_pending() == [ref]
    assert inbox.read_email(ref).body == "Question"
