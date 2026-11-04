"""
Which mailbox the agent works with, chosen with MAIL_PROVIDER:

    gmail    Gmail and Google Workspace (default)          -> gmail_client.py
    outlook  Outlook.com, Hotmail and Microsoft 365        -> outlook_client.py
    imap     iCloud, Yahoo, AOL, Zoho, GMX, Proton and any IMAP mailbox -> imap_client.py

All of them fulfill the Inbox protocol (process_inbox.py), so the rest of the agent
doesn't change. Adding a provider = a new class with list_pending, read_email and
create_draft (and start_watch if it has push notifications) + one line here.

Run it with: uv run --env-file .env python inboxes.py   (lists what still needs a reply)
"""

import os

PROVIDERS = ["gmail", "outlook", "imap"]


def open_inbox():
    """Connects to the configured mailbox. Imports lazily: each provider has its own libraries."""
    provider = os.environ.get("MAIL_PROVIDER", "gmail")
    if provider == "gmail":
        from gmail_client import GmailInbox

        return GmailInbox.connect()
    if provider == "outlook":
        from outlook_client import OutlookInbox

        return OutlookInbox.connect()
    if provider == "imap":
        from imap_client import ImapInbox

        return ImapInbox.connect()
    raise ValueError(f"MAIL_PROVIDER must be one of {PROVIDERS}, not {provider!r}")


if __name__ == "__main__":
    inbox = open_inbox()
    ids = inbox.list_pending()
    print(f"{os.environ.get('MAIL_PROVIDER', 'gmail')}: emails that still need a reply: {ids}")
    for message_id in ids:
        mail = inbox.read_email(message_id)
        print(f"- From: {mail.sender} | Subject: {mail.subject} | {mail.body[:80]!r}")
