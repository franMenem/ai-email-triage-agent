"""
Endpoint tests: valid email, missing field, duplicate. Run them with:
    uv run python test_main.py

They DON'T call the real Claude API: `draft_reply` is replaced by a fake
that always returns the same draft and records who called it.
So the tests are free, fast and don't need internet.
"""

from fastapi.testclient import TestClient

import main
from models import Draft

calls = []


async def fake_draft_reply(email):
    calls.append(email.message_id)
    return Draft(
        draft="TEST DRAFT",
        internal_notes="TEST NOTES",
        intent="complaint",
        urgency="high",
    )


client = TestClient(main.app)


def test_valid_email_is_received():
    main.draft_reply = fake_draft_reply  # the endpoint will use the fake
    mail = {
        "message_id": "abc-1",
        "sender": "customer@example.com",
        "subject": "Question about my order",
        "body": "Hi, when does my order arrive?",
    }
    response = client.post("/emails", json=mail)
    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "message_id": "abc-1",
        "draft": "TEST DRAFT",
        "internal_notes": "TEST NOTES",
        "intent": "complaint",
        "urgency": "high",
    }


def test_missing_field_returns_422_and_skips_llm():
    main.draft_reply = fake_draft_reply
    incomplete = {"message_id": "abc-2", "sender": "customer@example.com"}
    response = client.post("/emails", json=incomplete)
    assert response.status_code == 422
    assert "abc-2" not in calls


def test_duplicate_does_not_call_llm_again():
    main.draft_reply = fake_draft_reply
    mail = {
        "message_id": "abc-3",
        "sender": "customer@example.com",
        "subject": "Complaint",
        "body": "The product arrived broken.",
    }
    first = client.post("/emails", json=mail)
    second = client.post("/emails", json=mail)
    assert first.json()["draft"] == "TEST DRAFT"
    assert second.json() == {"status": "duplicate", "message_id": "abc-3"}
    assert calls.count("abc-3") == 1, "A duplicate must not spend another call"


if __name__ == "__main__":
    test_valid_email_is_received()
    test_missing_field_returns_422_and_skips_llm()
    test_duplicate_does_not_call_llm_again()
    print("Endpoint tests OK")
