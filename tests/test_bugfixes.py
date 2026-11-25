"""
One test (or a few) per bug found in the audit: each one failed before its fix.
No network and no API calls: the same fakes as the rest of the suite.
"""

import asyncio
import base64
import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

import agent
import main
from agent.models import Email
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
    from types import SimpleNamespace as NS

    from agent import drafter

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
