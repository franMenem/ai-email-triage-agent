"""Gmail parsing (gmail_parsing.py): HTML-only emails, quoted replies, thread context. No API calls."""

import base64

from gmail_parsing import (
    email_to_answer,
    extract_text,
    format_thread,
    has_newer_draft,
    html_to_text,
    strip_quoted,
)


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


def message(msg_id, sender, body):
    return {
        "id": msg_id,
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": b64(body)},
            "headers": [{"name": "From", "value": sender}, {"name": "Date", "value": "Mon, 2 Nov 2026"}],
        },
    }


def test_extract_text_prefers_plain_text():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": b64("Plain version")}},
        ],
    }
    assert extract_text(payload) == "Plain version"


def test_extract_text_falls_back_to_html():
    html = "<html><style>p {color: red}</style><body><p>Hi Fran,</p><p>Can we talk?</p></body></html>"
    payload = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": b64(html)}}]}
    text = extract_text(payload)
    assert "Hi Fran," in text and "Can we talk?" in text
    assert "color" not in text, "CSS must not end up in the text"


def test_html_to_text_keeps_line_breaks():
    assert html_to_text("one<br>two") == "one\ntwo"


def test_strip_quoted_removes_the_quoted_reply():
    body = "Tuesday works!\n\nOn Mon, 2 Nov 2026, Fran <f@x.com> wrote:\n> Tuesday or Thursday?\n> Fran"
    assert strip_quoted(body) == "Tuesday works!"


def test_strip_quoted_spanish_header():
    body = "Dale, el martes.\n\nEl lun, 2 nov 2026, Fran <f@x.com> escribió:\n> ¿Martes o jueves?"
    assert strip_quoted(body) == "Dale, el martes."


def test_format_thread_skips_the_current_message_and_keeps_order():
    messages = [
        message("1", "Mark <k@x.com>", "Can we meet?"),
        message("2", "Fran <f@x.com>", "Tuesday or Thursday?"),
        message("3", "Mark <k@x.com>", "Tuesday works"),
    ]
    thread = format_thread(messages, current_id="3")
    assert thread.index("Can we meet?") < thread.index("Tuesday or Thursday?")
    assert "Tuesday works" not in thread


def test_format_thread_is_empty_for_a_first_message():
    assert format_thread([message("1", "Mark <k@x.com>", "Hi")], current_id="1") == ""


def test_format_thread_ignores_drafts():
    draft = message("2", "Fran <f@x.com>", "Unsent draft text")
    draft["labelIds"] = ["DRAFT"]
    messages = [message("1", "Mark <k@x.com>", "Can we meet?"), draft, message("3", "Mark <k@x.com>", "Hello?")]
    assert "Unsent draft text" not in format_thread(messages, current_id="3")


def test_a_draft_written_after_the_email_answers_it():
    thread = [
        {"id": "1", "internalDate": "1000", "labelIds": ["INBOX"]},
        {"id": "d", "internalDate": "2000", "labelIds": ["DRAFT"]},
    ]
    assert has_newer_draft(thread, "1")


def test_an_older_draft_does_not_hide_a_new_email():
    # The client wrote again before the first draft was sent
    thread = [
        {"id": "1", "internalDate": "1000", "labelIds": ["INBOX"]},
        {"id": "d", "internalDate": "2000", "labelIds": ["DRAFT"]},
        {"id": "2", "internalDate": "3000", "labelIds": ["INBOX", "UNREAD"]},
    ]
    assert not has_newer_draft(thread, "2")


def test_no_draft_means_pending():
    assert not has_newer_draft([{"id": "1", "internalDate": "1000", "labelIds": ["INBOX"]}], "1")


def msg(msg_id, date, *labels):
    return {"id": msg_id, "internalDate": str(date), "labelIds": list(labels)}


def test_a_read_email_still_needs_a_reply():
    # Opening an email isn't answering it
    assert email_to_answer([msg("1", 1000, "INBOX")]) == "1"


def test_the_latest_client_email_is_the_one_to_answer():
    thread = [msg("1", 1000, "INBOX"), msg("2", 2000, "SENT"), msg("3", 3000, "INBOX", "UNREAD")]
    assert email_to_answer(thread) == "3"


def test_nothing_to_answer_after_you_replied():
    assert email_to_answer([msg("1", 1000, "INBOX"), msg("2", 2000, "SENT")]) is None


def test_nothing_to_answer_when_the_draft_is_already_there():
    assert email_to_answer([msg("1", 1000, "INBOX"), msg("d", 2000, "DRAFT")]) is None


def test_a_new_email_after_an_unsent_draft_needs_its_own_reply():
    thread = [msg("1", 1000, "INBOX"), msg("d", 2000, "DRAFT"), msg("2", 3000, "INBOX")]
    assert email_to_answer(thread) == "2"
