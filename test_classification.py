"""
Intent and urgency classification. Run them with:
    uv run python test_classification.py   (doesn't call the API: it's free)

The allowed values live in two places that must match: `Draft` in models.py
(what Python accepts) and DRAFT_SCHEMA in drafter.py (what Claude may answer).
"""

from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from drafter import DRAFT_SCHEMA
from models import Draft

INTENTS = ["order_status", "return_request", "complaint", "general_inquiry", "other"]
URGENCIES = ["high", "medium", "low"]


def test_draft_accepts_valid_values():
    d = Draft(draft="x", internal_notes="y", intent="complaint", urgency="high")
    assert d.intent == "complaint"
    assert d.urgency == "high"


def test_draft_rejects_made_up_values():
    try:
        Draft(draft="x", internal_notes="y", intent="grievance", urgency="high")
        assert False, "intent='grievance' doesn't exist: it should raise ValidationError"
    except ValidationError:
        pass
    try:
        Draft(draft="x", internal_notes="y", intent="other", urgency="urgent")
        assert False, "urgency='urgent' doesn't exist: it should raise ValidationError"
    except ValidationError:
        pass


def test_schema_matches_the_model():
    props = DRAFT_SCHEMA["properties"]
    assert sorted(props["intent"]["enum"]) == sorted(INTENTS)
    assert sorted(props["urgency"]["enum"]) == sorted(URGENCIES)
    for field in ["draft", "internal_notes", "intent", "urgency"]:
        assert field in DRAFT_SCHEMA["required"], f"'{field}' is missing from required"
    assert DRAFT_SCHEMA["additionalProperties"] is False


def test_endpoint_returns_the_classification():
    async def fake_draft_reply(email):
        return Draft(draft="D", internal_notes="N", intent="return_request", urgency="medium")

    main.draft_reply = fake_draft_reply
    mail = {
        "message_id": "class-1",
        "sender": "customer@example.com",
        "subject": "I want to return it",
        "body": "It's too small, how do I return it?",
    }
    data = TestClient(main.app).post("/emails", json=mail).json()
    assert data["intent"] == "return_request"
    assert data["urgency"] == "medium"


if __name__ == "__main__":
    test_draft_accepts_valid_values()
    test_draft_rejects_made_up_values()
    test_schema_matches_the_model()
    test_endpoint_returns_the_classification()
    print("Classification tests OK")
