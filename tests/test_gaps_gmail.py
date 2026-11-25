"""Proposed tests: gmail_parsing.py, mail_core.py and gmail_client.py (fake Gmail service, no network)."""

import base64
import email
from datetime import datetime

import pytest

import gmail_client
from gmail_client import GmailInbox
from gmail_parsing import build_reply, email_to_answer, extract_text, format_thread
from mail_core import build_reply_message, html_to_text, looks_automated, strip_quoted
from mail_core import format_thread as core_format_thread


def b64(text, encoding="utf-8"):
    return base64.urlsafe_b64encode(text.encode(encoding)).decode()


def gmail_message(
    msg_id,
    sender,
    body,
    date="Mon, 2 Nov 2026",
    labels=("INBOX",),
    internal=1000,
    extra=None,
    subject="Report",
):
    headers = [
        {"name": "From", "value": sender},
        {"name": "Date", "value": date},
        {"name": "Subject", "value": subject},
    ]
    headers += [{"name": k, "value": v} for k, v in (extra or {}).items()]
    return {
        "id": msg_id,
        "threadId": "t1",
        "internalDate": str(internal),
        "labelIds": list(labels),
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": b64(body)},
            "headers": headers,
        },
    }


# ---------- gmail_parsing.extract_text ----------


def test_extract_text_decodes_urlsafe_base64():
    # "???" encodes to "Pz8_" in the URL-safe alphabet ("Pz8/" in the standard one)
    data = b64("¿Qué??? ñandú >>>???")
    assert "_" in data or "-" in data
    assert (
        extract_text({"mimeType": "text/plain", "body": {"data": data}})
        == "¿Qué??? ñandú >>>???"
    )


def test_extract_text_survives_bytes_that_are_not_utf8():
    data = base64.urlsafe_b64encode("Hola señor".encode("latin-1")).decode()
    assert extract_text({"mimeType": "text/plain", "body": {"data": data}}).startswith(
        "Hola se"
    )


def test_extract_text_finds_the_body_nested_under_an_attachment():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            # A .txt attachment: text/plain, but its content is not inline (only an attachmentId)
            {
                "mimeType": "text/plain",
                "filename": "notes.txt",
                "body": {"attachmentId": "A1", "size": 10},
            },
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Nested plain")}},
                    {
                        "mimeType": "text/html",
                        "body": {"data": b64("<p>Nested html</p>")},
                    },
                ],
            },
            {
                "mimeType": "application/pdf",
                "filename": "a.pdf",
                "body": {"attachmentId": "A2", "size": 99},
            },
        ],
    }
    assert extract_text(payload) == "Nested plain"


def test_extract_text_of_an_email_without_body_is_an_empty_string():
    assert extract_text({"mimeType": "multipart/mixed", "parts": []}) == ""
    assert extract_text({"mimeType": "text/plain", "body": {"size": 0}}) == ""


# ---------- gmail_parsing.format_thread / to_thread_messages / build_reply ----------


def test_format_thread_exact_text_with_sender_and_date():
    messages = [
        gmail_message("1", "Mark <k@x.com>", "Can we meet?", date="Mon, 2 Nov 2026"),
        gmail_message("2", "Fran <f@x.com>", "Tuesday?", date="Tue, 3 Nov 2026"),
        gmail_message("3", "Mark <k@x.com>", "Ok"),
    ]
    assert format_thread(messages, current_id="3") == (
        "From: Mark <k@x.com>\nDate: Mon, 2 Nov 2026\nCan we meet?\n---\n"
        "From: Fran <f@x.com>\nDate: Tue, 3 Nov 2026\nTuesday?"
    )


def test_a_client_email_archived_out_of_the_inbox_still_needs_a_reply():
    # A Gmail filter with "Skip the Inbox + apply label": the message has no INBOX label
    assert (
        email_to_answer(
            [{"id": "1", "internalDate": "1000", "labelIds": ["Label_7", "UNREAD"]}]
        )
        == "1"
    )


def test_email_to_answer_goes_by_date_not_by_list_position():
    newest_first = [
        {"id": "3", "internalDate": "3000", "labelIds": ["INBOX"]},
        {"id": "2", "internalDate": "2000", "labelIds": ["SENT"]},
    ]
    assert email_to_answer(newest_first) == "3"


def test_build_reply_is_urlsafe_base64_of_a_threaded_reply():
    text = "".join(chr(c) for c in range(32, 127)) * 3 + " ¿¿¿???>>>"
    raw = build_reply(
        {"from": "Mark <k@x.com>", "subject": "Report", "message-id": "<a1@x>"}, text
    )
    assert "+" not in raw and "/" not in raw
    reply = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert (reply["To"], reply["Subject"], reply["In-Reply-To"]) == (
        "Mark <k@x.com>",
        "Re: Report",
        "<a1@x>",
    )


# ---------- mail_core ----------


@pytest.mark.parametrize(
    "headers",
    [
        {"from": "Shop <hi@shop.com>", "precedence": "Bulk"},
        {"from": "Ana <ana@x.com>", "auto-submitted": "auto-replied"},
        {"from": "GitHub <noreply@github.com>"},
        {"from": "ACME <No-Reply@acme.com>"},
        {"from": "Mail Delivery <MAILER-DAEMON@mx.google.com>"},
        {"from": "LinkedIn <notifications@linkedin.com>"},
        {"from": "x <bounce-123@mailer.com>"},
    ],
)
def test_looks_automated_true(headers):
    assert looks_automated(headers)


@pytest.mark.parametrize(
    "headers",
    [
        {"from": "Mark <mark@x.com>", "auto-submitted": "no"},
        {"from": "Mark <mark@x.com>", "precedence": "normal"},
        {
            "from": "No Reply Fan <mark@x.com>"
        },  # only the address counts, not the display name
    ],
)
def test_looks_automated_false(headers):
    assert not looks_automated(headers)


def test_html_to_text_paragraphs_scripts_and_blank_lines():
    html = "<div>Hola</div><script>alert('x')</script><p>Chau</p>\n\n\n\n<p>  </p><ul><li>uno</li><li>dos</li></ul>"
    assert html_to_text(html) == "Hola\nChau\n\nuno\ndos"


def test_strip_quoted_drops_quote_lines_without_a_header():
    assert strip_quoted("Dale\n> lo que dijiste\n  > otra\nSaludos") == "Dale\nSaludos"


def test_strip_quoted_drops_everything_after_the_quote_header():
    assert (
        strip_quoted(
            "Hi\n\nOn Mon, 2 Nov 2026, Fran wrote:\nold text without marker\nmore"
        )
        == "Hi"
    )


@pytest.mark.parametrize(
    "separator",
    [
        "________________________________",
        "-----Original Message-----",
        "From: Mark <k@x.com>",
    ],
)
def test_strip_quoted_outlook_separators(separator):
    assert (
        strip_quoted(f"Ok, gracias.\n\n{separator}\nSent: Monday\nTexto viejo")
        == "Ok, gracias."
    )


def test_thread_keeps_the_last_10_messages_and_2000_chars_each():
    entries = [(f"s{i}@x.com", f"d{i}", f"body-{i}-" + "x" * 3000) for i in range(12)]
    text = core_format_thread(entries)
    blocks = text.split("\n---\n")
    assert len(blocks) == 10
    assert blocks[0].startswith("From: s2@x.com\nDate: d2\nbody-2-") and blocks[
        -1
    ].startswith("From: s11@x.com")
    assert all(len(b.split("\n", 2)[2]) == 2000 for b in blocks)


def test_thread_messages_lose_their_own_quotes():
    assert (
        core_format_thread([("a@x.com", "d", "New\n> old")])
        == "From: a@x.com\nDate: d\nNew"
    )


def test_reply_goes_to_reply_to_and_keeps_the_references_chain():
    reply = build_reply_message(
        {
            "from": "Mark <k@x.com>",
            "reply-to": "soporte@x.com",
            "subject": "RE: Report",
            "message-id": "<3@x>",
            "references": "<1@x> <2@x>",
        },
        "Hola, va la respuesta.",
    )
    assert reply["To"] == "soporte@x.com"
    assert reply["Subject"] == "RE: Report"
    assert reply["In-Reply-To"] == "<3@x>"
    assert reply["References"] == "<1@x> <2@x> <3@x>"
    assert reply.get_content().strip() == "Hola, va la respuesta."


def test_reply_to_an_email_without_subject_or_message_id():
    reply = build_reply_message({"from": "k@x.com"}, "Hola")
    assert reply["Subject"] == "Re: " or reply["Subject"] == "Re:"
    assert reply["In-Reply-To"] is None and reply["References"] is None


def test_first_reply_references_the_original_message_id():
    reply = build_reply_message(
        {"from": "k@x.com", "subject": "Hi", "message-id": "<1@x>"}, "Hola"
    )
    assert reply["References"] == "<1@x>"


# ---------- gmail_client.GmailInbox with a fake Gmail service ----------


class _Call:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeGmail:
    """service.users().threads().list(...).execute() and friends, over in-memory threads."""

    def __init__(self, threads, labels=()):
        self.threads_by_id = threads  # thread id -> [messages]
        self.label_list = list(labels)
        self.queries, self.created, self.watch_body, self.thread_gets = [], [], None, []

    def users(self):
        return self

    def threads(self):
        return self

    def messages(self):
        return _FakeMessages(self)

    def drafts(self):
        return _FakeDrafts(self)

    def labels(self):
        return _FakeLabels(self)

    def list(self, userId, q=None, **kw):
        self.queries.append(q)
        return _Call({"threads": [{"id": t} for t in self.threads_by_id]})

    def get(self, userId, id, format=None, metadataHeaders=None):
        self.thread_gets.append((id, format))
        return _Call({"id": id, "messages": self.threads_by_id[id]})

    def watch(self, userId, body):
        self.watch_body = body
        return _Call({"historyId": "1", "expiration": "1794000000000"})


class _FakeMessages:
    def __init__(self, fake):
        self.fake = fake

    def get(self, userId, id, format=None):
        for tid, messages in self.fake.threads_by_id.items():
            for m in messages:
                if m["id"] == id:
                    return _Call({**m, "threadId": tid})
        raise KeyError(id)


class _FakeDrafts:
    def __init__(self, fake):
        self.fake = fake

    def create(self, userId, body):
        self.fake.created.append(body)
        return _Call({"id": "r1", "message": {"id": "d1"}})


class _FakeLabels:
    def __init__(self, fake):
        self.fake = fake

    def list(self, userId):
        return _Call({"labels": self.fake.label_list})


def test_gmail_list_pending_asks_for_the_label_and_the_last_7_days(monkeypatch):
    monkeypatch.setattr(gmail_client, "AGENT_LABEL", "agente")
    newsletter = gmail_message(
        "n1",
        "News <news@shop.com>",
        "50% off",
        extra={"List-Unsubscribe": "<mailto:u@x>"},
    )
    fake = FakeGmail(
        {
            "t1": [gmail_message("m1", "Mark <k@x.com>", "Hi", internal=1000)],
            "t2": [
                gmail_message("m2", "Ana <a@x.com>", "Hi", internal=1000),
                gmail_message(
                    "m3", "Fran <f@x.com>", "Re", labels=("SENT",), internal=2000
                ),
            ],
            # With a label, the user chose these emails: newsletters are NOT skipped
            "t3": [newsletter],
        }
    )
    assert GmailInbox(fake).list_pending() == ["m1", "n1"]
    assert fake.queries == ["label:agente newer_than:7d"]


def test_gmail_all_mail_mode_reads_primary_and_skips_automated(monkeypatch):
    monkeypatch.setattr(gmail_client, "AGENT_LABEL", "*")
    fake = FakeGmail(
        {
            "t1": [gmail_message("m1", "Mark <k@x.com>", "Hi")],
            "t2": [
                gmail_message(
                    "n1",
                    "News <news@shop.com>",
                    "50% off",
                    extra={"List-Unsubscribe": "<mailto:u@x>"},
                )
            ],
            "t3": [gmail_message("n2", "Bank <no-reply@bank.com>", "Statement")],
        }
    )
    assert GmailInbox(fake).list_pending() == ["m1"]
    assert fake.queries == ["in:inbox category:primary newer_than:7d"]


def test_gmail_read_email_gives_sender_subject_body_without_quote_and_thread():
    first = gmail_message("m1", "Mark <k@x.com>", "Can we meet?", internal=1000)
    mine = gmail_message(
        "m2", "Fran <f@x.com>", "Tuesday?", labels=("SENT",), internal=2000
    )
    last = gmail_message(
        "m3",
        '"Mark K." <k@x.com>',
        "Tuesday works\n\nOn Mon, 2 Nov 2026, Fran wrote:\n> Tuesday?",
        internal=3000,
        subject="Re: Report",
    )
    mail = GmailInbox(FakeGmail({"t1": [first, mine, last]})).read_email("m3")
    assert (mail.message_id, mail.sender, mail.subject, mail.body) == (
        "m3",
        "k@x.com",
        "Re: Report",
        "Tuesday works",
    )
    assert mail.thread.index("Can we meet?") < mail.thread.index("Tuesday?")
    assert "Tuesday works" not in mail.thread


def test_gmail_read_email_without_subject_or_body():
    bare = {
        "id": "m1",
        "internalDate": "1000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [],
            "headers": [{"name": "From", "value": "k@x.com"}],
        },
    }
    mail = GmailInbox(FakeGmail({"t1": [bare]})).read_email("m1")
    assert (mail.sender, mail.subject, mail.body, mail.thread) == (
        "k@x.com",
        "",
        "",
        "",
    )


def test_gmail_create_draft_stays_in_the_thread():
    original = gmail_message(
        "m1", "Mark <k@x.com>", "Hi", extra={"Message-ID": "<a1@x>"}
    )
    fake = FakeGmail({"t9": [original]})
    GmailInbox(fake).create_draft("m1", "Tuesday works.")
    [body] = fake.created
    assert body["message"]["threadId"] == "t9"
    reply = email.message_from_bytes(base64.urlsafe_b64decode(body["message"]["raw"]))
    assert (reply["To"], reply["Subject"], reply["In-Reply-To"]) == (
        "Mark <k@x.com>",
        "Re: Report",
        "<a1@x>",
    )
    assert "Tuesday works." in reply.get_payload()


def test_gmail_label_id_and_watch(monkeypatch):
    fake = FakeGmail(
        {},
        labels=[{"id": "INBOX", "name": "INBOX"}, {"id": "Label_12", "name": "agente"}],
    )
    inbox = GmailInbox(fake)
    assert inbox.label_id("agente") == "Label_12"
    with pytest.raises(ValueError):
        inbox.label_id("no-existe")

    monkeypatch.setattr(gmail_client, "AGENT_LABEL", "agente")
    expiry = inbox.start_watch()
    assert (
        fake.watch_body["labelIds"] == ["Label_12"]
        and fake.watch_body["labelFilterBehavior"] == "include"
    )
    assert expiry == datetime.fromtimestamp(1794000000)

    monkeypatch.setattr(gmail_client, "AGENT_LABEL", "*")
    inbox.start_watch()
    assert fake.watch_body["labelIds"] == ["INBOX"]
