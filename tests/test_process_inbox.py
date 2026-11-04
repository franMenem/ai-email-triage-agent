"""
The processing flow with a fake inbox and a fake Claude: which emails get a draft,
what happens when Claude fails. No Gmail, no API calls.
"""

import asyncio

import anthropic
import httpx

from models import Draft, Email
from process_inbox import process_new_emails


class FakeInbox:
    """Fulfills the Inbox protocol in memory."""

    def __init__(self, pending):
        self.pending = pending
        self.drafts = {}

    def list_pending(self):
        return list(self.pending)

    def read_email(self, message_id):
        return Email(message_id=message_id, sender="mark@x.com", subject="Hi", body=f"body {message_id}")

    def create_draft(self, message_id, text):
        self.drafts[message_id] = text


async def fake_claude(mail, profile):
    return Draft(draft=f"reply to {mail.body}", internal_notes="n", intent="other", urgency="low")


def test_every_pending_email_gets_its_draft():
    inbox = FakeInbox(["1", "2"])
    asyncio.run(process_new_emails("freelance", inbox, draft_fn=fake_claude))
    assert inbox.drafts == {"1": "reply to body 1", "2": "reply to body 2"}


def test_nothing_pending_means_no_claude_calls():
    calls = []

    async def counting_claude(mail, profile):
        calls.append(mail.message_id)

    asyncio.run(process_new_emails("freelance", FakeInbox([]), draft_fn=counting_claude))
    assert calls == []


def test_a_claude_error_skips_that_email_but_not_the_rest():
    async def flaky_claude(mail, profile):
        if mail.message_id == "1":
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
        return await fake_claude(mail, profile)

    inbox = FakeInbox(["1", "2"])
    asyncio.run(process_new_emails("freelance", inbox, draft_fn=flaky_claude))
    assert "1" not in inbox.drafts, "No draft, so the next run retries it"
    assert inbox.drafts["2"] == "reply to body 2"


def test_the_profile_reaches_claude():
    seen = []

    async def recording_claude(mail, profile):
        seen.append(profile)
        return await fake_claude(mail, profile)

    asyncio.run(process_new_emails("store", FakeInbox(["1"]), draft_fn=recording_claude))
    assert seen == ["store"]
