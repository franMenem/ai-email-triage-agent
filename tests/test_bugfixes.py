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
