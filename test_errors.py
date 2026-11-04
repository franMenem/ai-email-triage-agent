"""
What happens when the Claude API fails. Run them with:
    uv run python test_errors.py

- Temporary errors (no connection, 429 rate limit, 5xx) -> 503: retry later.
- Permanent errors (e.g. 401 invalid API key) -> 500: something must be fixed.
- An email whose Claude call failed is NOT marked as seen, so it can be retried.

The SDK already retries on its own (max_retries=2), so when one of these
errors reaches the endpoint, 3 attempts were made in total.
"""

import anthropic
import httpx
from fastapi.testclient import TestClient

import main
from models import Draft

client = TestClient(main.app)

# Fake request the SDK exceptions need to be built
REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def failing_fake(error):
    """Returns a fake draft_reply that always raises `error`."""

    async def fake(email):
        raise error

    return fake


async def fake_ok(email):
    return Draft(
        draft="DRAFT OK",
        internal_notes="NOTES OK",
        intent="general_inquiry",
        urgency="high",
    )


def mail(message_id):
    return {
        "message_id": message_id,
        "sender": "customer@example.com",
        "subject": "Question",
        "body": "Hi, when does my order arrive?",
    }


def test_no_connection_returns_503_and_can_retry():
    main.draft_reply = failing_fake(anthropic.APIConnectionError(request=REQ))
    response = client.post("/emails", json=mail("err-1"))
    assert response.status_code == 503, f"Expected 503, got {response.status_code}"

    # Anthropic is back: the same email must be processed, not "duplicate"
    main.draft_reply = fake_ok
    retry = client.post("/emails", json=mail("err-1"))
    assert retry.status_code == 200
    assert retry.json()["status"] == "received", (
        "The failed email was marked as seen: it was lost forever"
    )


def test_rate_limit_returns_503():
    error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=REQ), body=None
    )
    main.draft_reply = failing_fake(error)
    response = client.post("/emails", json=mail("err-2"))
    assert response.status_code == 503


def test_anthropic_server_error_returns_503():
    error = anthropic.InternalServerError(
        "boom", response=httpx.Response(500, request=REQ), body=None
    )
    main.draft_reply = failing_fake(error)
    response = client.post("/emails", json=mail("err-3"))
    assert response.status_code == 503


def test_invalid_api_key_returns_500_and_can_retry():
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=REQ), body=None
    )
    main.draft_reply = failing_fake(error)
    response = client.post("/emails", json=mail("err-4"))
    assert response.status_code == 500

    main.draft_reply = fake_ok
    retry = client.post("/emails", json=mail("err-4"))
    assert retry.json()["status"] == "received"


if __name__ == "__main__":
    test_no_connection_returns_503_and_can_retry()
    test_rate_limit_returns_503()
    test_anthropic_server_error_returns_503()
    test_invalid_api_key_returns_500_and_can_retry()
    print("Error handling tests OK")
