"""
Processes the inbox: for each email the agent handles that still needs a reply,
Claude writes it and it is saved as a DRAFT in the same conversation.
It never sends anything: you review the draft and send it.

Run it with: uv run --env-file .env python process_inbox.py
(uses the Claude API: each new email is a paid call; the mailbox is MAIL_PROVIDER, see inboxes.py)

The profile (prompt + tools) is chosen with --profile; "freelance" by default:
    uv run --env-file .env python process_inbox.py --profile store

To keep it running and check the inbox every N minutes (Ctrl+C stops it):
    uv run --env-file .env python process_inbox.py --every 5
"""

import argparse
import asyncio
from typing import Protocol

import anthropic

from drafter import draft_reply
from inboxes import open_inbox
from models import Email
from profiles import PROFILES


class Inbox(Protocol):
    """What the processing needs from an inbox. GmailInbox, OutlookInbox and
    ImapInbox fulfill it (see inboxes.py), and the tests pass a fake one: this
    file doesn't depend on any mail provider."""

    def list_pending(self) -> list[str]: ...
    def read_email(self, message_id: str) -> Email: ...
    def create_draft(self, message_id: str, text: str): ...


async def process_new_emails(profile, inbox: Inbox, draft_fn=draft_reply):
    # The mail clients are blocking: asyncio.to_thread runs each call in a worker
    # thread, so the server keeps answering other requests while it waits.
    # Only emails without a newer draft come back: no state file needed.
    new_ids = await asyncio.to_thread(inbox.list_pending)
    print(f"Profile: {profile} | New emails to process: {len(new_ids)}")

    for message_id in new_ids:
        mail = await asyncio.to_thread(inbox.read_email, message_id)
        print(f"\n— From: {mail.sender} | Subject: {mail.subject}")
        try:
            draft = await draft_fn(mail, profile)
        except anthropic.APIError as e:
            # No draft is created, so the next run retries it
            print(f"  Claude API error, will retry: {e}")
            continue

        await asyncio.to_thread(inbox.create_draft, message_id, draft.draft)
        print(f"  Intent: {draft.intent} | Urgency: {draft.urgency}")
        print(f"  Internal notes: {draft.internal_notes}")
        print("  ✅ Draft created")


async def watch(profile, minutes):
    """Processes the inbox forever, pausing `minutes` between rounds."""
    while True:
        try:
            inbox = await asyncio.to_thread(open_inbox)
            await process_new_emails(profile, inbox)
        except Exception as e:
            # Here any error is caught (e.g. the internet went down and the mailbox failed):
            # the process must stay alive and retry on the next round.
            print(f"Error in this round, retrying in {minutes} min: {e!r}")
        print(f"\nNext check in {minutes} min (Ctrl+C to stop)")
        await asyncio.sleep(minutes * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES, default="freelance")
    parser.add_argument("--every", type=int, help="minutes between checks; without it, runs once")
    args = parser.parse_args()
    if args.every:
        asyncio.run(watch(args.profile, args.every))
    else:
        asyncio.run(process_new_emails(args.profile, open_inbox()))
