"""
HTTP endpoint: POST /emails receives an email as JSON and returns Claude's
draft and classification. Used for the store demo and the tests.

Idempotent: an email whose message_id was already processed is not sent to
Claude again ("duplicate"). If the Claude call fails, the email is not marked
as seen, so it can be retried.

Run it with: uv run --env-file .env fastapi dev main.py
and open http://127.0.0.1:8000/docs
"""

import anthropic
from fastapi import FastAPI, HTTPException

from drafter import draft_reply
from models import Email

app = FastAPI()
# A set, because the only question is "have I seen this ID?"
seen_ids = set()


@app.post("/emails")
async def receive(email: Email):
    try:
        if email.message_id not in seen_ids:
            draft = await draft_reply(email)
            seen_ids.add(email.message_id)
            return {
                "status": "received",
                "message_id": email.message_id,
                "draft": draft.draft,
                "internal_notes": draft.internal_notes,
                "intent": draft.intent,
                "urgency": draft.urgency,
            }
        else:
            return {
                "status": "duplicate",
                "message_id": email.message_id,
            }
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=503, detail="Claude API is unreachable, try again later")
    except anthropic.APIStatusError as e:
        # 429 and 5xx are temporary: worth retrying later. The rest needs a fix.
        if e.status_code == 429 or e.status_code >= 500:
            raise HTTPException(status_code=503, detail="Claude API is busy, try again later")
        else:
            raise HTTPException(status_code=500, detail="The service is not available right now")
