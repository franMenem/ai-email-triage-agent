"""
Gmail webhook and watch renewal: token check and background processing.
No Gmail and no API calls: the inbox is swapped for a fake one through FastAPI's
dependency_overrides, and the processing is swapped for a recorder.
"""

import base64
import json
import logging

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
TOKEN = "test-token"


class FakeInbox:
    def start_watch(self):
        return {"historyId": "1", "expiration": "1762871000000"}


@pytest.fixture(autouse=True)
def fake_gmail(monkeypatch):
    """Every test gets a fake inbox, and records how many times it was opened."""
    opened = []

    def factory():
        opened.append(1)
        return FakeInbox()

    main.app.dependency_overrides[main.get_inbox_factory] = lambda: factory
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    yield opened
    main.app.dependency_overrides.clear()


def push_body(history_id="123"):
    data = base64.b64encode(json.dumps({"emailAddress": "me@x.com", "historyId": history_id}).encode())
    return {"message": {"data": data.decode(), "messageId": "m-1"}, "subscription": "projects/p/subscriptions/s"}


def test_wrong_or_missing_token_is_rejected_before_touching_gmail(fake_gmail):
    assert client.post("/gmail-webhook?token=wrong", json=push_body()).status_code == 403
    assert client.post("/gmail-webhook", json=push_body()).status_code == 403
    assert client.post("/renew-watch?token=wrong").status_code == 403
    assert fake_gmail == [], "A request with a wrong token must never open Gmail"


def test_webhook_is_closed_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("WEBHOOK_TOKEN", raising=False)
    assert client.post("/gmail-webhook?token=", json=push_body()).status_code == 503


def test_webhook_answers_204_and_processes_the_inbox(monkeypatch, fake_gmail):
    monkeypatch.setenv("AGENT_PROFILE", "store")
    calls = []

    async def fake_process(profile, inbox):
        calls.append((profile, type(inbox).__name__))

    monkeypatch.setattr(main, "process_new_emails", fake_process)
    response = client.post(f"/gmail-webhook?token={TOKEN}", json=push_body())
    assert response.status_code == 204
    assert calls == [("store", "FakeInbox")], "Processed once, with AGENT_PROFILE and the injected inbox"


def test_webhook_survives_a_processing_error(monkeypatch):
    async def failing_process(profile, inbox):
        raise RuntimeError("Gmail is down")

    monkeypatch.setattr(main, "process_new_emails", failing_process)
    assert client.post(f"/gmail-webhook?token={TOKEN}", json=push_body()).status_code == 204


def test_webhook_accepts_a_message_without_data(monkeypatch):
    async def fake_process(profile, inbox):
        pass

    monkeypatch.setattr(main, "process_new_emails", fake_process)
    body = {"message": {"messageId": "m-2"}}
    assert client.post(f"/gmail-webhook?token={TOKEN}", json=body).status_code == 204


def test_renew_watch():
    response = client.post(f"/renew-watch?token={TOKEN}")
    assert response.status_code == 200
    assert response.json()["status"] == "watch renewed"


def test_access_logs_hide_the_token():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:0", "POST", "/gmail-webhook?token=secret123&x=1", "1.1", 204), None,
    )
    main.HideTokenInAccessLogs().filter(record)
    line = record.getMessage()
    assert "secret123" not in line
    assert "/gmail-webhook?token=***&x=1" in line
