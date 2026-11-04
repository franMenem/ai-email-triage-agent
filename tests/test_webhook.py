"""
Gmail webhook and watch renewal: token check and background processing.
No API calls: the inbox processing and the Gmail watch are replaced by fakes.
"""

import base64
import json

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)
TOKEN = "test-token"


def push_body(history_id="123"):
    data = base64.b64encode(json.dumps({"emailAddress": "me@x.com", "historyId": history_id}).encode())
    return {"message": {"data": data.decode(), "messageId": "m-1"}, "subscription": "projects/p/subscriptions/s"}


def test_webhook_rejects_a_wrong_or_missing_token(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    assert client.post("/gmail-webhook?token=wrong", json=push_body()).status_code == 403
    assert client.post("/gmail-webhook", json=push_body()).status_code == 403


def test_webhook_is_closed_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("WEBHOOK_TOKEN", raising=False)
    assert client.post("/gmail-webhook?token=", json=push_body()).status_code == 503


def test_webhook_answers_204_and_processes_the_inbox(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setenv("AGENT_PROFILE", "store")
    calls = []

    async def fake_process(profile):
        calls.append(profile)

    monkeypatch.setattr(main, "process_new_emails", fake_process)
    response = client.post(f"/gmail-webhook?token={TOKEN}", json=push_body())
    assert response.status_code == 204
    assert calls == ["store"], "The inbox must be processed once, with AGENT_PROFILE"


def test_webhook_survives_a_processing_error(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)

    async def failing_process(profile):
        raise RuntimeError("Gmail is down")

    monkeypatch.setattr(main, "process_new_emails", failing_process)
    assert client.post(f"/gmail-webhook?token={TOKEN}", json=push_body()).status_code == 204


def test_webhook_accepts_a_message_without_data(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)

    async def fake_process(profile):
        pass

    monkeypatch.setattr(main, "process_new_emails", fake_process)
    body = {"message": {"messageId": "m-2"}}
    assert client.post(f"/gmail-webhook?token={TOKEN}", json=body).status_code == 204


def test_renew_watch(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(main, "connect", lambda: "service")
    monkeypatch.setattr(main, "start_watch", lambda service: {"historyId": "1", "expiration": "1762871000000"})
    assert client.post("/renew-watch?token=wrong").status_code == 403
    response = client.post(f"/renew-watch?token={TOKEN}")
    assert response.status_code == 200
    assert response.json()["status"] == "watch renewed"


def test_access_logs_hide_the_token():
    import logging

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:0", "POST", "/gmail-webhook?token=secret123&x=1", "1.1", 204), None,
    )
    main.HideTokenInAccessLogs().filter(record)
    line = record.getMessage()
    assert "secret123" not in line
    assert "/gmail-webhook?token=***&x=1" in line
