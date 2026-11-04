"""
HTTP endpoints.

- POST /emails: receives an email as JSON and returns Claude's draft and
  classification. Used for the store demo and the tests. Idempotent: an email
  whose message_id was already processed is not sent to Claude again
  ("duplicate"); if the Claude call fails, it's not marked as seen.

- POST /gmail-webhook: Gmail -> Pub/Sub -> here, when an email gets the agent
  label. Answers right away (Pub/Sub only needs a 2xx) and processes the inbox
  in the background, the same way process_inbox.py does.

- POST /renew-watch: renews the Gmail watch, which expires after 7 days.
  Something must call it every day (see the deploy section of the README).

The last two are public URLs, so they require ?token=<WEBHOOK_TOKEN>.

Run it with: uv run --env-file .env fastapi dev main.py
and open http://127.0.0.1:8000/docs
"""

import asyncio
import base64
import json
import os
import secrets
from datetime import datetime

import anthropic
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from drafter import draft_reply
from gmail_client import connect, start_watch
from models import Email
from process_inbox import process_new_emails

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


def check_token(token: str):
    """Only callers that know WEBHOOK_TOKEN (Pub/Sub and the daily renewal) get in."""
    expected = os.environ.get("WEBHOOK_TOKEN")
    if not expected:
        # Fail closed: without a configured token, nobody gets in
        raise HTTPException(status_code=503, detail="WEBHOOK_TOKEN is not configured")
    # compare_digest takes the same time whether the token is right or wrong,
    # so it can't be guessed character by character from the response time
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid token")


class PubSubMessage(BaseModel):
    data: str = ""  # base64 JSON from Gmail: {"emailAddress": ..., "historyId": ...}
    messageId: str = ""


class PubSubPush(BaseModel):
    """What a Pub/Sub push subscription POSTs to the endpoint."""

    message: PubSubMessage
    subscription: str = ""


# One processing round at a time: if two notifications arrive together, the second
# waits, and then finds that the first one already left the drafts (so it skips them).
processing_lock = asyncio.Lock()


async def process_in_background():
    profile = os.environ.get("AGENT_PROFILE", "freelance")
    async with processing_lock:
        try:
            await process_new_emails(profile)
        except Exception as e:
            # Pub/Sub already got its 2xx, so it won't retry: the email stays pending
            # (unread, labeled, no draft) and the next notification picks it up.
            print(f"Error processing the inbox: {e!r}")


@app.post("/gmail-webhook", status_code=204)
async def gmail_webhook(push: PubSubPush, background_tasks: BackgroundTasks, token: str = ""):
    check_token(token)
    # The notification doesn't include the email, only "something changed".
    # It's logged, and the inbox is checked the usual way: labeled, unread, no draft.
    try:
        notification = json.loads(base64.b64decode(push.message.data))
        print(f"Gmail notification: historyId={notification.get('historyId')}")
    except (ValueError, TypeError):
        print(f"Pub/Sub message {push.message.messageId} without readable data")
    # Pub/Sub expects an answer within seconds; Claude can take longer, so the
    # work runs after the response is sent
    background_tasks.add_task(process_in_background)


@app.post("/renew-watch")
def renew_watch(token: str = ""):
    check_token(token)
    response = start_watch(connect())
    expires = datetime.fromtimestamp(int(response["expiration"]) / 1000)
    return {"status": "watch renewed", "expires": expires.isoformat(timespec="minutes")}
