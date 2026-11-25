# Privacy policy

This project is a personal tool. It runs on its owner's own mailbox and is not offered as a service to other people. This page describes what it does with data when someone runs it on their own account.

## What it accesses

With the permission you grant through Google sign-in, the app can:

- **read** the emails in your mailbox that match the filter you configured (by default, the ones with the label `agente`), together with the earlier messages of the same thread;
- **create drafts** in your mailbox.

It cannot send email, delete email or change your settings: it doesn't request those permissions.

## What it does with the data

- The text of each email it handles (sender, subject, body and thread) is sent to the Anthropic API so that Claude can write a draft reply. Anthropic processes it under its own terms and privacy policy.
- The draft is saved in your mailbox, inside the original thread. Nothing is sent to anyone: a person reviews every draft.
- The app keeps no database of your emails and writes no copy of them to disk. The server logs record the sender and subject of the emails it processed, and error messages.
- Your Google sign-in token is stored where you run the app (a local file, or an environment variable on your server). It is never committed to this repository.

## What it doesn't do

It doesn't sell, share or use your data for advertising, and it doesn't use it to train models. The use of data received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Removing access

You can revoke the app's access at any time at <https://myaccount.google.com/permissions>. Deleting the token file or environment variable stops it from working.

## Contact

Questions: open an issue at <https://github.com/franMenem/ai-email-triage-agent/issues>.
