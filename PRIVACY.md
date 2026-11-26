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
- If you set the Langfuse keys (`LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`), usage data is also sent to [Langfuse](https://langfuse.com), a third party that stores it under its own terms and privacy policy. It is **off by default**: without the keys nothing is sent. The project uses the US region (`us.cloud.langfuse.com`).
  - What is sent: the model name, token counts, timings, the intent, urgency and `needs_reply` that Claude assigned, the names of the tools that ran, and the profile used.
  - What is not sent: the text of the email, the sender, the subject, what the tools returned and the draft. The attributes that could carry them are deleted before anything leaves the server.
  - The only exception is the evals, which send full content because their emails are made up.
- The app keeps no database of your emails and writes no copy of them to disk. The server logs record the sender and subject of the emails it processed, and error messages.
- Your Google sign-in token is stored where you run the app (a local file, or an environment variable on your server). It is never committed to this repository.

## The public web demo

If the owner turns on the web demo (`/demo`, only with an access code), whoever uses it types or pastes an email and some business information into a web page. That text is sent to the Anthropic API to write the draft, under Anthropic's own terms and privacy policy. It is **not stored**: the server keeps it in memory only while the request runs, and it is not written to disk or to the logs (neither is the access code). If Langfuse is on, the demo traces carry only metrics, never the text. The server keeps in memory only how many drafts each access code used today. Please don't paste real sensitive data into the demo (IDs, health information, passwords, real employee or candidate records): use made-up examples.

## What it doesn't do

It doesn't sell, share or use your data for advertising, and it doesn't use it to train models. The use of data received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Removing access

You can revoke the app's access at any time at <https://myaccount.google.com/permissions>. Deleting the token file or environment variable stops it from working.

## Contact

Questions: open an issue at <https://github.com/franMenem/ai-email-triage-agent/issues>.
