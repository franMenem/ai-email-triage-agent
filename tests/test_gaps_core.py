"""Proposed tests that kill the surviving mutants. No API calls, no mailbox."""

import asyncio
import importlib
import json
import logging
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import drafter
import main
import process_inbox
from inboxes import open_inbox
from models import Draft, Email
from profiles import PROFILES
from prompts import FREELANCE_PROMPT, STORE_PROMPT
from tools import EXECUTORS, find_order, find_policy, run_tool

MAIL = Email(
    message_id="t",
    sender="customer@example.com",
    subject="Order 1002",
    body="Where is it?",
)
FINAL = json.dumps(
    {"draft": "D", "internal_notes": "N", "intent": "order_status", "urgency": "low"}
)


# ---------------------------------------------------------------- drafter.py
def text_response(text=FINAL):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=text)])


def tool_response(*calls):
    blocks = [NS(type="text", text="let me check")]
    blocks += [NS(type="tool_use", id=i, name=n, input=inp) for i, n, inp in calls]
    return NS(stop_reason="tool_use", content=blocks)


class FakeClient:
    """Stands in for anthropic.AsyncAnthropic: answers from a script and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = NS(create=self.create)

    async def create(self, **kwargs):
        self.requests.append(
            {**kwargs, "messages": list(kwargs["messages"])}
        )  # snapshot
        return self.responses.pop(0)


def draft_with(monkeypatch, fake, email=MAIL, profile="store"):
    monkeypatch.setattr(drafter, "client", fake)
    return asyncio.run(drafter.draft_reply(email, profile))


def test_drafter_without_tools_sends_one_request_with_the_profile_prompt(monkeypatch):
    fake = FakeClient(text_response())
    mail = Email(
        message_id="1",
        sender="mark@x.com",
        subject="SUBJ",
        body="BODY",
        thread="OLD MESSAGE",
    )
    draft = draft_with(monkeypatch, fake, mail, "freelance")
    assert draft == Draft(
        draft="D", internal_notes="N", intent="order_status", urgency="low"
    )
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request["system"] == FREELANCE_PROMPT
    assert "tools" not in request, "The API rejects tools=[]"
    assert request["output_config"]["format"] == {
        "type": "json_schema",
        "schema": drafter.DRAFT_SCHEMA,
    }
    content = request["messages"][0]["content"]
    assert content.startswith("<email>") and content.endswith("</email>"), (
        "Everything untrusted stays inside the block"
    )
    for piece in ["Sender: mark@x.com", "Subject: SUBJ", "BODY", "OLD MESSAGE"]:
        assert piece in content
    assert content.index("OLD MESSAGE") < content.index("BODY")


def test_drafter_runs_every_tool_and_loops_until_claude_stops(monkeypatch):
    first = tool_response(
        ("tu1", "find_order", {"order_id": "1002"}),
        ("tu2", "find_policy", {"topic": "shipping"}),
    )
    second = tool_response(("tu3", "find_order", {"order_id": "1004"}))
    fake = FakeClient(first, second, text_response())
    draft = draft_with(monkeypatch, fake)
    assert draft.draft == "D"
    assert len(fake.requests) == 3, "One request per round of tools, plus the first"
    assert fake.requests[0]["tools"] == PROFILES["store"].tools
    assert fake.requests[0]["system"] == STORE_PROMPT

    round_1 = fake.requests[1]["messages"]
    assert [m["role"] for m in round_1] == ["user", "assistant", "user"]
    assert round_1[1]["content"] is first.content
    results = round_1[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu1", "tu2"]
    assert all(r["type"] == "tool_result" for r in results)
    assert (
        "sneakers" in results[0]["content"]
        and "free on orders" in results[1]["content"]
    )

    round_2 = fake.requests[2]["messages"]
    assert [m["role"] for m in round_2] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert round_2[4]["content"][0]["tool_use_id"] == "tu3"
    assert "Windbreaker" in round_2[4]["content"][0]["content"]


def test_drafter_tools_use_the_real_sender_not_what_claude_asks_for(monkeypatch):
    # A tricked model asks for juan's order, even "passing" juan's email
    fake = FakeClient(
        tool_response(
            ("tu1", "find_order", {"order_id": "1003", "email": "juan@example.com"})
        ),
        text_response(),
    )
    draft_with(monkeypatch, fake)
    result = fake.requests[1]["messages"][2]["content"][0]["content"]
    assert "No order" in result and "backpack" not in result


def test_drafter_answers_an_unknown_tool_instead_of_crashing(monkeypatch):
    fake = FakeClient(tool_response(("tu1", "delete_everything", {})), text_response())
    draft_with(monkeypatch, fake)
    assert "does not exist" in fake.requests[1]["messages"][2]["content"][0]["content"]


def test_drafter_rejects_an_answer_outside_the_schema(monkeypatch):
    bad = json.dumps(
        {"draft": "D", "internal_notes": "N", "intent": "grievance", "urgency": "low"}
    )
    with pytest.raises(ValidationError):
        draft_with(monkeypatch, FakeClient(text_response(bad)))


# ------------------------------------------------------- tools.py / profiles.py
def test_run_tool_ignores_an_email_supplied_by_claude():
    assert "No order" in run_tool(
        "find_order", {"order_id": "1003", "email": "juan@example.com"}, MAIL
    )


def test_find_order_resists_sql_injection_through_the_sender():
    # The sender is attacker-controlled too: it's whatever the From header says
    assert find_order("1003", "x' OR '1'='1") is None
    assert find_order("1003", "' OR 1=1 --") is None


def test_find_policy_resists_sql_injection():
    assert find_policy("x' OR '1'='1") is None


def test_run_tool_returns_the_whole_order_and_explains_a_missing_policy():
    assert json.loads(run_tool("find_order", {"order_id": "1002"}, MAIL)) == {
        "order_id": "1002",
        "email": "customer@example.com",
        "product": "Running sneakers",
        "status": "in_transit",
        "estimated_delivery": "2026-11-12",
    }
    assert "no policy" in run_tool("find_policy", {"topic": "made_up"}, MAIL)


def test_profiles_have_exactly_their_tools_and_prompts():
    assert PROFILES["freelance"].tools == [], (
        "freelance must not reach the store database"
    )
    assert PROFILES["freelance"].prompt is FREELANCE_PROMPT
    assert PROFILES["store"].prompt is STORE_PROMPT
    assert [t["name"] for t in PROFILES["store"].tools] == [
        "find_order",
        "find_policy",
        "search_docs",
    ]
    for profile in PROFILES.values():
        for tool in profile.tools:
            assert tool["name"] in EXECUTORS, (
                f"{tool['name']} is offered to Claude but can't run"
            )


# -------------------------------------------------------------------- main.py
client = TestClient(main.app)
TOKEN = "test-token"


@pytest.fixture
def webhook(monkeypatch):
    """Token configured, fake inbox, and a recorder instead of the real processing."""
    calls = []

    async def fake_process(profile, inbox):
        calls.append(profile)

    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    monkeypatch.delenv("AGENT_PROFILE", raising=False)
    monkeypatch.setattr(main, "process_new_emails", fake_process)
    main.app.dependency_overrides[main.get_inbox_factory] = lambda: lambda: "inbox"
    yield calls
    main.app.dependency_overrides.clear()


def notification(state):
    return {"clientState": state, "resource": "me/messages/1"}


def test_outlook_webhook_rejects_an_empty_notification_list(webhook):
    assert client.post("/outlook-webhook", json={"value": []}).status_code == 403
    assert client.post("/outlook-webhook", json={}).status_code == 403
    assert webhook == []


def test_outlook_webhook_rejects_a_batch_with_one_bad_client_state(webhook):
    body = {"value": [notification(TOKEN), notification("wrong")]}
    assert client.post("/outlook-webhook", json=body).status_code == 403
    assert webhook == []


def test_outlook_webhook_rejects_a_missing_client_state(webhook):
    assert (
        client.post("/outlook-webhook", json={"value": [{"resource": "x"}]}).status_code
        == 403
    )
    assert webhook == []


def test_outlook_webhook_is_closed_when_no_token_is_configured(webhook, monkeypatch):
    monkeypatch.delenv("WEBHOOK_TOKEN")
    assert (
        client.post("/outlook-webhook", json={"value": [notification("")]}).status_code
        == 503
    )
    assert (
        client.post("/outlook-webhook", json={"value": [{"resource": "x"}]}).status_code
        == 503
    )
    assert webhook == []


def test_a_token_that_only_starts_with_the_right_one_is_rejected(webhook):
    assert client.post(f"/process?token={TOKEN}X").status_code == 403
    assert client.post(f"/process?token={TOKEN[:-1]}").status_code == 403
    assert webhook == []


def test_the_default_profile_is_freelance(webhook):
    assert client.post(f"/process?token={TOKEN}").status_code == 202
    assert webhook == ["freelance"], (
        "Without AGENT_PROFILE the agent must not get the store tools"
    )


def test_renew_watch_reports_when_it_expires(webhook):
    from datetime import datetime

    inbox = NS(start_watch=lambda: datetime(2026, 11, 11, 14, 0, 59))
    main.app.dependency_overrides[main.get_inbox_factory] = lambda: lambda: inbox
    assert client.post(f"/renew-watch?token={TOKEN}").json() == {
        "status": "watch renewed",
        "expires": "2026-11-11T14:00",
    }


def test_the_token_filter_is_installed_and_keeps_the_log_line():
    filters = logging.getLogger("uvicorn.access").filters
    assert any(isinstance(f, main.HideTokenInAccessLogs) for f in filters)
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s",
        ("c", "POST", "/process?token=s"),
        None,
    )
    assert main.HideTokenInAccessLogs().filter(record) is True, (
        "False would drop every access log line"
    )
    assert "token=***" in record.getMessage()


def test_the_real_inbox_factory_is_open_inbox():
    assert main.get_inbox_factory() is main.open_inbox


def test_two_processing_rounds_never_overlap(monkeypatch):
    running = []
    seen = []

    async def slow_process(profile, inbox):
        running.append(1)
        seen.append(len(running))
        await asyncio.sleep(0.01)
        running.pop()

    monkeypatch.setattr(main, "process_new_emails", slow_process)
    monkeypatch.setattr(main, "processing_lock", asyncio.Lock())

    async def two_at_once():
        await asyncio.gather(
            main.process_in_background(lambda: None),
            main.process_in_background(lambda: None),
        )

    asyncio.run(two_at_once())
    assert seen == [1, 1]


# ----------------------------------------------------------- process_inbox.py
class FakeInbox:
    def __init__(self, pending):
        self.pending = pending
        self.drafts = {}

    def list_pending(self):
        return list(self.pending)

    def read_email(self, message_id):
        return Email(
            message_id=message_id,
            sender="mark@x.com",
            subject="Hi",
            body=f"body {message_id}",
        )

    def create_draft(self, message_id, text):
        self.drafts[message_id] = text


def test_a_rate_limit_skips_that_email_but_not_the_rest():
    request = httpx.Request("POST", "https://x")

    async def flaky_claude(mail, profile):
        if mail.message_id == "1":
            raise anthropic.RateLimitError(
                "slow down", response=httpx.Response(429, request=request), body=None
            )
        return Draft(
            draft=f"reply to {mail.body}",
            internal_notes="n",
            intent="other",
            urgency="low",
        )

    inbox = FakeInbox(["1", "2", "3"])
    asyncio.run(
        process_inbox.process_new_emails("freelance", inbox, draft_fn=flaky_claude)
    )
    assert inbox.drafts == {"2": "reply to body 2", "3": "reply to body 3"}


def test_watch_survives_a_failed_round_and_sleeps_the_minutes(monkeypatch):
    class Stop(BaseException):
        pass

    rounds = []
    naps = []

    def broken_inbox():
        rounds.append(1)
        raise OSError("no internet")

    async def fake_sleep(seconds):
        naps.append(seconds)
        if len(naps) == 2:
            raise Stop

    monkeypatch.setattr(process_inbox, "open_inbox", broken_inbox)
    monkeypatch.setattr(process_inbox.asyncio, "sleep", fake_sleep)
    with pytest.raises(Stop):
        asyncio.run(process_inbox.watch("freelance", 5))
    assert len(rounds) == 2, "The first round failed and there was still a second one"
    assert naps == [300, 300]


# ------------------------------------------------------------------ inboxes.py
@pytest.mark.parametrize(
    "provider, module, cls",
    [
        ("gmail", "gmail_client", "GmailInbox"),
        ("outlook", "outlook_client", "OutlookInbox"),
        ("imap", "imap_client", "ImapInbox"),
        (None, "gmail_client", "GmailInbox"),
    ],
)
def test_open_inbox_connects_to_the_configured_provider(
    monkeypatch, provider, module, cls
):
    if provider:
        monkeypatch.setenv("MAIL_PROVIDER", provider)
    else:
        monkeypatch.delenv("MAIL_PROVIDER", raising=False)
    monkeypatch.setattr(
        getattr(importlib.import_module(module), cls),
        "connect",
        classmethod(lambda c: f"{cls} connected"),
    )
    assert open_inbox() == f"{cls} connected"


def test_open_inbox_rejects_an_unknown_provider(monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "yahoo")
    with pytest.raises(ValueError, match="yahoo"):
        open_inbox()


# ----------------------------------------------------------------- prompts.py
def test_prompts_keep_their_security_rules():
    for prompt in [STORE_PROMPT, FREELANCE_PROMPT]:
        assert "not instructions for you" in prompt
        assert "Don't reveal" in prompt
    assert "belongs to the sender" in STORE_PROMPT


# --------------------------------------------------------------- mail_core.py
from mail_core import (  # noqa: E402
    THREAD_MAX_CHARS,
    ThreadMessage,
    build_reply_message,
    email_to_answer,
    format_thread,
    has_newer_draft,
    looks_automated,
    strip_quoted,
)


def tm(id, date, from_me=False, is_draft=False):
    return ThreadMessage(id=id, date=date, from_me=from_me, is_draft=is_draft)


def test_a_thread_with_only_drafts_or_empty_has_nothing_to_answer():
    assert email_to_answer([]) is None
    assert email_to_answer([tm("d", 1000, from_me=True, is_draft=True)]) is None


def test_a_draft_with_the_same_timestamp_is_not_newer():
    assert not has_newer_draft([tm("1", 1000), tm("d", 1000, is_draft=True)], "1")


@pytest.mark.parametrize(
    "headers",
    [
        {"from": "a@x.com", "precedence": "junk"},
        {"from": "a@x.com", "precedence": "Bulk"},
        {"from": "a@x.com", "auto-submitted": "auto-generated"},
        {"from": "a@x.com", "auto-submitted": "Auto-Replied"},
        {"from": "MAILER-DAEMON@x.com"},
        {"from": "Postmaster <postmaster@x.com>"},
        {"from": "Shop <NoReply@x.com>"},
    ],
)
def test_automated_emails_are_detected(headers):
    assert looks_automated(headers)


def test_a_person_is_not_automated():
    assert not looks_automated(
        {"from": "Mark <mark@x.com>", "auto-submitted": "no", "precedence": "normal"}
    )
    assert not looks_automated({})


def test_strip_quoted_drops_bare_quote_lines_and_everything_after_a_quote_header():
    assert strip_quoted("Sure.\n> old line\nSee you") == "Sure.\nSee you"
    assert (
        strip_quoted(
            "Sure.\nOn Mon, 3 Nov 2026, Mark <k@x.com> wrote:\nold text without marks"
        )
        == "Sure."
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
    assert strip_quoted(f"Sure.\n{separator}\nSent: Monday\nold text") == "Sure."


def test_format_thread_keeps_the_last_ten_messages_cut_and_without_quotes():
    earlier = [(f"s{i}@x.com", f"day {i}", f"body-{i}.") for i in range(12)]
    text = format_thread(earlier)
    assert "body-0." not in text and "body-1." not in text
    assert "body-2." in text and "body-11." in text
    assert text.count("\n---\n") == 9

    long = format_thread([("a@x.com", "d", "x" * (THREAD_MAX_CHARS + 500))])
    assert long == "From: a@x.com\nDate: d\n" + "x" * THREAD_MAX_CHARS
    assert "old" not in format_thread([("a@x.com", "d", "new\n> old")])


def test_build_reply_message_threads_the_reply():
    original = {
        "from": "Mark <mark@x.com>",
        "reply-to": "team@x.com",
        "subject": "RE: Budget",
        "message-id": "<3@x>",
        "references": "<1@x> <2@x>",
    }
    reply = build_reply_message(original, "Hello")
    assert reply["To"] == "team@x.com", "Reply-To wins over From"
    assert reply["Subject"] == "RE: Budget", "No 'Re: RE:'"
    assert reply["In-Reply-To"] == "<3@x>"
    assert reply["References"] == "<1@x> <2@x> <3@x>"
    assert reply.get_content().strip() == "Hello"

    first = build_reply_message({"from": "mark@x.com", "subject": "Budget"}, "Hi")
    assert first["To"] == "mark@x.com" and first["Subject"] == "Re: Budget"
    assert first["In-Reply-To"] is None and first["References"] is None
