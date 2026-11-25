"""
One test (or a few) per bug found in the audit: each one failed before its fix.
No network and no API calls: the same fakes as the rest of the suite.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import main

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
