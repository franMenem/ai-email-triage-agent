"""
Processes the inbox: for each new email with the agent label, Claude writes
the reply and it is saved as a DRAFT in the same Gmail thread.
It never sends anything: you review the draft and send it.

Run it with: uv run --env-file .env python process_inbox.py
(uses the Claude API: each new email is a paid call)

The profile (prompt + tools) is chosen with --profile; "freelance" by default:
    uv run --env-file .env python process_inbox.py --profile store

To keep it running and check the inbox every N minutes (Ctrl+C stops it):
    uv run --env-file .env python process_inbox.py --every 5
"""

import argparse
import asyncio

import anthropic

from drafter import draft_reply
from gmail_client import connect, create_draft, list_pending, read_email
from profiles import PROFILES


async def main(profile):
    service = connect()
    # Only emails whose thread has no draft yet: no state file needed (see list_pending)
    new_ids = list_pending(service)
    print(f"Profile: {profile} | New emails to process: {len(new_ids)}")

    for message_id in new_ids:
        mail = read_email(service, message_id)
        print(f"\n— From: {mail.sender} | Subject: {mail.subject}")
        try:
            draft = await draft_reply(mail, profile)
        except anthropic.APIError as e:
            # No draft is created, so the next run retries it
            print(f"  Claude API error, will retry: {e}")
            continue

        create_draft(service, message_id, draft.draft)
        print(f"  Intent: {draft.intent} | Urgency: {draft.urgency}")
        print(f"  Internal notes: {draft.internal_notes}")
        print("  ✅ Draft created in Gmail")


async def watch(profile, minutes):
    """Runs main() forever, pausing `minutes` between rounds."""
    while True:
        try:
            await main(profile)
        except Exception as e:
            # Here any error is caught (e.g. the internet went down and Gmail failed):
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
        asyncio.run(main(args.profile))
