"""
One test (or a few) per bug found in the audit: each one failed before its fix.
No network and no API calls: the same fakes as the rest of the suite.
"""

import asyncio
import base64
import json
import sys
import types
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import agent
import main
import process_inbox
from agent import drafter
from agent.models import Draft, Email
from agent.tools import find_order, run_tool

TOKEN = "test-token"
# raise_server_exceptions=False: an unhandled error shows up as the 500 a real
# caller would get, instead of being raised inside the test.
client = TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def webhook(monkeypatch):
    """Token configured, fake inbox, and a recorder instead of the real processing."""
    calls = []

    async def fake_process(profile, inbox):
        calls.append(profile)

    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(main, "process_new_emails", fake_process)
    main.app.dependency_overrides[main.get_inbox_factory] = lambda: lambda: "inbox"
    yield calls
    main.app.dependency_overrides.clear()


# ------------------------------------------------- 1. /gmail-webhook, odd JSON
@pytest.mark.parametrize("payload", ["[1]", "123", "null", '"text"', "true"])
def test_gmail_webhook_takes_json_that_is_not_an_object_like_unreadable_data(
    webhook, payload
):
    def push(data):
        return {"message": {"data": data, "messageId": "m-1"}}

    unreadable = client.post(f"/gmail-webhook?token={TOKEN}", json=push("%%%"))
    assert unreadable.status_code == 204, "How broken data is already answered"

    data = base64.b64encode(payload.encode()).decode()
    response = client.post(f"/gmail-webhook?token={TOKEN}", json=push(data))
    assert response.status_code == unreadable.status_code, (
        "A 5xx would make Pub/Sub retry the same malformed message forever"
    )
    assert len(webhook) == 2, "The inbox is still checked, like for unreadable data"


# ------------------------------------------- 2. /outlook-webhook, odd bodies
@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"content": b"this is not json"},
        {"content": b"\xff\xfe"},
        {"json": [{"clientState": TOKEN}]},
        {"json": "text"},
        {"json": None},
        {"json": {"value": ["text"]}},
        {"json": {"value": [None]}},
        {"json": {"value": [{"clientState": TOKEN}, 7]}},
        {"json": {"value": "text"}},
        {"json": {"value": {"clientState": TOKEN}}},
    ],
)
def test_outlook_webhook_rejects_a_malformed_body_without_processing(
    webhook, request_kwargs
):
    response = client.post("/outlook-webhook", **request_kwargs)
    assert 400 <= response.status_code < 500
    assert webhook == [], "Nothing is processed for a malformed notification"


def test_outlook_webhook_still_accepts_a_good_notification(webhook):
    good = {"value": [{"clientState": TOKEN, "resource": "me/messages/1"}]}
    assert client.post("/outlook-webhook", json=good).status_code == 202
    assert len(webhook) == 1


# ------------------------------------------------ 3. tokens that are not ASCII
@pytest.mark.parametrize("path", ["/gmail-webhook", "/process", "/renew-watch"])
def test_a_non_ascii_token_is_rejected_like_any_wrong_token(webhook, path):
    wrong = client.post(f"{path}?token=wrong", json={"message": {}})
    assert wrong.status_code == 403
    response = client.post(path, params={"token": "é"}, json={"message": {}})
    assert response.status_code == wrong.status_code
    assert webhook == []


@pytest.mark.parametrize("state", ["é", "contraseña", "\ud800", 123, ["x"], {"a": 1}])
def test_a_client_state_that_is_not_ascii_text_is_rejected_like_a_wrong_one(
    webhook, state
):
    def post(client_state):
        body = json.dumps({"value": [{"clientState": client_state}]})
        return client.post("/outlook-webhook", content=body)

    assert post("wrong").status_code == 403
    assert post(state).status_code == 403
    assert webhook == []


def test_a_non_ascii_configured_token_still_works(webhook, monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "contraseña")
    assert client.post("/process", params={"token": "contraseña"}).status_code == 202
    assert client.post("/process", params={"token": "contrasena"}).status_code == 403
    good = {"value": [{"clientState": "contraseña"}]}
    assert client.post("/outlook-webhook", json=good).status_code == 202
    assert len(webhook) == 2


# ----------------------------------------- 4. find_order: case, spaces and "#"
ORDER_1002 = {
    "order_id": "1002",
    "email": "customer@example.com",
    "product": "Running sneakers",
    "status": "in_transit",
    "estimated_delivery": "2026-11-12",
}


@pytest.mark.parametrize(
    "order_id, email",
    [
        ("1002", "Customer@Example.com"),
        ("1002", "CUSTOMER@EXAMPLE.COM"),
        (" 1002", "customer@example.com"),
        ("1002\n", "customer@example.com"),
        ("#1002", "customer@example.com"),
        (" # 1002 ", "Customer@example.com"),
        (1002, "customer@example.com"),
    ],
)
def test_find_order_ignores_email_case_and_spaces_or_hash_in_the_number(
    order_id, email
):
    assert find_order(order_id, email) == ORDER_1002


@pytest.mark.parametrize(
    "order_id, email",
    [
        ("1003", "customer@example.com"),  # a real order, of someone else
        ("#1003", "Customer@Example.com"),
        ("1002", "juan@example.com"),  # a real customer, not the owner
        ("1002", "customer@example.co"),
        ("1002", ""),
        ("", "customer@example.com"),
        ("#", "customer@example.com"),
        ("##1002", "customer@example.com"),
        ("10 02", "customer@example.com"),
        ("%", "customer@example.com"),
        ("1002", "%"),
        ("1002", "customer@example.co_"),
    ],
)
def test_find_order_still_needs_the_number_and_the_sender_to_match(order_id, email):
    assert find_order(order_id, email) is None


@pytest.mark.parametrize(
    "order_id, email",
    [
        ("1002' OR '1'='1", "customer@example.com"),
        ("#1002' OR '1'='1", "Customer@Example.com"),
        ("1003' --", "customer@example.com"),
        ("1003", "x' OR '1'='1"),
        ("1003", "' OR 1=1 --"),
        ("1003", "X') OR LOWER('1')=LOWER('1"),
        ("1003", "juan@example.com' OR email LIKE '%"),
        ("' OR 1=1 --", "' OR 1=1 --"),
    ],
)
def test_find_order_resists_sql_injection_through_both_fields(order_id, email):
    assert find_order(order_id, email) is None
    mail = Email(message_id="t", sender=email, subject="", body="")
    assert "No order" in run_tool("find_order", {"order_id": order_id}, mail)


# ------------------------------------- 5. run_tool without the expected input
MAIL = Email(
    message_id="t",
    sender="customer@example.com",
    subject="Order 1002",
    body="Where is my order 1002? This is the email body.",
)


@pytest.fixture
def fake_rag(monkeypatch):
    """Stands in for agent/rag.py (which needs the optional model) and records the queries."""
    fake = types.ModuleType("agent.rag")
    fake.FAQ_DOCS = {"hours": "Open 9 to 6."}
    fake.calls = []

    def search_docs(query, index, docs, **kwargs):
        fake.calls.append(query)
        return ["Open 9 to 6."]

    fake.search_docs = search_docs
    fake.faq_index = lambda: "THE INDEX"
    monkeypatch.setitem(sys.modules, "agent.rag", fake)
    monkeypatch.setattr(agent, "rag", fake, raising=False)
    return fake


@pytest.mark.parametrize(
    "name, key",
    [("find_order", "order_id"), ("find_policy", "topic"), ("search_docs", "query")],
)
@pytest.mark.parametrize(
    "tool_input", [{}, {"other": "x"}, None, "1002", ["1002"], "null_value"]
)
def test_run_tool_explains_a_missing_input_to_claude_instead_of_crashing(
    fake_rag, name, key, tool_input
):
    if tool_input == "null_value":
        tool_input = {key: None}
    answer = run_tool(name, tool_input, MAIL)
    assert isinstance(answer, str)
    assert name in answer and key in answer, "Claude must know what to send"
    assert "sneakers" not in answer and "Open 9 to 6" not in answer
    assert fake_rag.calls == [], (
        "Nothing is searched without Claude's query: never the email body instead"
    )


def test_run_tool_still_runs_with_the_expected_input(fake_rag):
    assert "sneakers" in run_tool("find_order", {"order_id": "1002"}, MAIL)
    assert "sneakers" in run_tool("find_order", {"order_id": 1002}, MAIL)
    assert "free on orders" in run_tool("find_policy", {"topic": "shipping"}, MAIL)
    assert run_tool("search_docs", {"query": "hours?"}, MAIL) == "Open 9 to 6."
    assert fake_rag.calls == ["hours?"]
    assert "does not exist" in run_tool("delete_everything", {}, MAIL)


def test_a_real_search_error_is_not_turned_into_a_missing_input_or_no_documents(
    fake_rag, monkeypatch
):
    def not_installed(query, index, docs, **kwargs):
        raise ModuleNotFoundError("No module named 'sentence_transformers'")

    monkeypatch.setattr(fake_rag, "search_docs", not_installed)
    with pytest.raises(ModuleNotFoundError):
        run_tool("search_docs", {"query": "hours?"}, MAIL)


def test_a_missing_tool_input_does_not_kill_the_draft(monkeypatch):
    final = json.dumps(
        {"draft": "D", "internal_notes": "N", "intent": "other", "urgency": "low"}
    )
    responses = [
        NS(
            stop_reason="tool_use",
            content=[NS(type="tool_use", id="tu1", name="find_order", input={})],
        ),
        NS(stop_reason="end_turn", content=[NS(type="text", text=final)]),
    ]
    requests = []

    async def create(**kwargs):
        requests.append(list(kwargs["messages"]))
        return responses.pop(0)

    monkeypatch.setattr(drafter, "client", NS(messages=NS(create=create)))
    draft = asyncio.run(drafter.draft_reply(MAIL, "store"))
    assert draft.draft == "D"
    assert "order_id" in requests[1][2]["content"][0]["content"]


# --------------------------- 6. draft_reply: endless tool loop, answer w/o text
FINAL = json.dumps(
    {"draft": "D", "internal_notes": "N", "intent": "other", "urgency": "low"}
)


def asks_for_a_tool():
    block = NS(type="tool_use", id="tu", name="find_policy", input={"topic": "returns"})
    return NS(stop_reason="tool_use", content=[block])


def final_answer():
    return NS(stop_reason="end_turn", content=[NS(type="text", text=FINAL)])


class ScriptedClaude:
    """Stands in for the Anthropic client: answers from `script`; once it runs out,
    it keeps asking for a tool, like a model stuck in a loop."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.messages = NS(create=self.create)

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls > 50:
            raise AssertionError("draft_reply never stops calling Claude")
        return self.script.pop(0) if self.script else asks_for_a_tool()


def run_draft(monkeypatch, fake):
    monkeypatch.setattr(drafter, "client", fake)
    return asyncio.run(drafter.draft_reply(MAIL, "store"))


def test_a_model_stuck_asking_for_tools_is_stopped(monkeypatch):
    assert drafter.MAX_TOOL_ROUNDS == 5
    fake = ScriptedClaude()
    with pytest.raises(drafter.DraftError, match="tool"):
        run_draft(monkeypatch, fake)
    assert fake.calls == drafter.MAX_TOOL_ROUNDS + 1, (
        "The first request, one more per round of tools, and then it gives up"
    )


def test_the_last_allowed_round_of_tools_still_gets_its_draft(monkeypatch):
    rounds = [asks_for_a_tool() for _ in range(drafter.MAX_TOOL_ROUNDS)]
    fake = ScriptedClaude(*rounds, final_answer())
    assert run_draft(monkeypatch, fake).draft == "D"
    assert fake.calls == drafter.MAX_TOOL_ROUNDS + 1


@pytest.mark.parametrize(
    "stop_reason, content",
    [
        ("max_tokens", []),
        ("refusal", []),
        ("max_tokens", [NS(type="thinking", thinking="...")]),
        ("end_turn", []),
    ],
)
def test_an_answer_without_text_raises_a_clear_error(monkeypatch, stop_reason, content):
    fake = ScriptedClaude(NS(stop_reason=stop_reason, content=content))
    with pytest.raises(drafter.DraftError, match=stop_reason):
        run_draft(monkeypatch, fake)


# ------------------------- 7. one email that fails must not stop the others
class FlakyInbox:
    """Three pending emails; reading or drafting the ones listed fails."""

    def __init__(self, fail_read=(), fail_draft=()):
        self.fail_read, self.fail_draft = fail_read, fail_draft
        self.drafts = {}

    def list_pending(self):
        return ["1", "2", "3"]

    def read_email(self, message_id):
        if message_id in self.fail_read:
            raise RuntimeError("the mailbox failed reading this email")
        return Email(message_id=message_id, sender="m@x.com", subject="Hi", body="b")

    def create_draft(self, message_id, text):
        if message_id in self.fail_draft:
            raise RuntimeError("the mailbox failed saving this draft")
        self.drafts[message_id] = text


def claude_failing_on(message_id, error):
    async def draft_fn(mail, profile):
        if mail.message_id == message_id:
            raise error() if callable(error) else error
        return Draft(draft="ok", internal_notes="n", intent="other", urgency="low")

    return draft_fn


def not_a_draft():
    """What draft_reply raises when Claude's JSON doesn't fit `Draft`."""
    try:
        Draft.model_validate_json('{"draft": "only this"}')
    except ValidationError as e:
        return e


@pytest.mark.parametrize(
    "error",
    [
        drafter.DraftError("Claude still asks for tools after 5 rounds"),
        not_a_draft,
        ModuleNotFoundError("No module named 'sentence_transformers'"),
        KeyError("something unexpected"),
    ],
)
def test_an_email_claude_cannot_draft_is_skipped_and_the_rest_go_on(error, capsys):
    inbox = FlakyInbox()
    draft_fn = claude_failing_on("1", error)
    asyncio.run(process_inbox.process_new_emails("store", inbox, draft_fn=draft_fn))
    assert inbox.drafts == {"2": "ok", "3": "ok"}, (
        "No draft for the failed email (so the next round retries it), the rest done"
    )
    assert "will retry" in capsys.readouterr().out, "The error is printed, not hidden"


def test_an_email_that_cannot_be_read_is_skipped_and_the_rest_go_on(capsys):
    inbox = FlakyInbox(fail_read=["1"])
    draft_fn = claude_failing_on(None, None)
    asyncio.run(process_inbox.process_new_emails("store", inbox, draft_fn=draft_fn))
    assert inbox.drafts == {"2": "ok", "3": "ok"}
    assert "the mailbox failed reading" in capsys.readouterr().out


def test_a_draft_that_cannot_be_saved_does_not_stop_the_rest(capsys):
    inbox = FlakyInbox(fail_draft=["2"])
    draft_fn = claude_failing_on(None, None)
    asyncio.run(process_inbox.process_new_emails("store", inbox, draft_fn=draft_fn))
    assert inbox.drafts == {"1": "ok", "3": "ok"}
    out = capsys.readouterr().out
    assert "the mailbox failed saving" in out
    assert out.count("Draft created") == 2, "Not announced for the one that failed"


def test_a_failing_list_pending_still_fails_the_round():
    class Down(FlakyInbox):
        def list_pending(self):
            raise RuntimeError("the mailbox is down")

    with pytest.raises(RuntimeError, match="down"):
        asyncio.run(process_inbox.process_new_emails("store", Down()))


def test_emails_endpoint_answers_500_when_claude_gives_no_draft(monkeypatch):
    async def no_draft(email):
        raise drafter.DraftError("Claude's answer has no text (stop_reason: refusal)")

    async def works(email):
        return Draft(draft="ok", internal_notes="n", intent="other", urgency="low")

    body = {"message_id": "bugfix-7", "sender": "a@b.com", "subject": "s", "body": "b"}
    monkeypatch.setattr(main, "draft_reply", no_draft)
    response = client.post("/emails", json=body)
    assert response.status_code == 500
    assert response.json() == {"detail": "The service is not available right now"}, (
        "Same answer as the other errors that retrying won't fix; no internals leaked"
    )
    monkeypatch.setattr(main, "draft_reply", works)
    assert client.post("/emails", json=body).json()["status"] == "received", (
        "A failed email is not marked as seen"
    )
