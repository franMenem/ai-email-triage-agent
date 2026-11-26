"""
Claude writes the draft reply for an email.

Uses the async client, the system prompt and tools of the chosen profile
(profiles.py) and a structured output, so the result always matches `Draft`.
While Claude asks for tools, they run (tools.py) and the results go back to it.
"""

import re
from dataclasses import replace

import anthropic

from agent import tracing
from agent.models import Draft, Email
from agent.profiles import PROFILES, Profile
from agent.prompts import DEMO_SECTION
from agent.tools import run_tool

# Created once per module; the API key comes from ANTHROPIC_API_KEY
client = anthropic.AsyncAnthropic()
# Default model, chosen with the evals (see "Evals" in README.md): on those checks it is
# not distinguishable from Sonnet or Opus, and it's the cheapest and fastest. The evals
# can pass another one.
MODEL = "claude-haiku-5-5"

# The structured-output schema comes from the Pydantic model: one source of truth
DRAFT_SCHEMA = Draft.model_json_schema()
# How many times Claude may ask for tools for one email. Each round is another paid
# request, so a model stuck asking for tools must not go on forever.
MAX_TOOL_ROUNDS = 5
# A draft is up to 150 words plus the notes: the public demo doesn't need more than this
DEMO_MAX_TOKENS = 2000


# What goes in the draft when the email doesn't deserve a reply. A draft is still
# saved: it is what marks the email as processed (there is no state file), and it
# shows the reviewer that the agent read it and why it wrote nothing.
NO_REPLY_NOTE = (
    "[NO REPLY DRAFTED - DO NOT SEND]\n"
    "The agent classified this email as one that doesn't need a reply (spam, "
    "advertising or an automated message), so it wrote no answer.\n"
    "Delete this draft, or write the reply yourself if the classification is wrong.\n\n"
    "Why: {reason}"
)


class DraftError(Exception):
    """Claude didn't produce a draft for this email (the API itself answered fine)."""


async def call_claude(
    messages, profile: Profile, model: str, max_tokens: int = 16000
):
    # The API doesn't accept tools=[]: if the profile has no tools, the parameter is left out
    extra = {"tools": profile.tools} if profile.tools else {}
    return await client.messages.create(
        max_tokens=max_tokens,
        model=model,
        system=profile.prompt,
        messages=messages,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": DRAFT_SCHEMA,
            }
        },
        **extra,
    )


async def draft_reply(
    email: Email,
    profile: str = "store",
    model: str = MODEL,
    trace: list | None = None,
    business_info: str | None = None,
) -> Draft:
    """Drafts the reply to one email. If `trace` is a list, every tool call is appended
    to it as {"tool", "input", "result"}: the evals use it to check what Claude did
    instead of taking its word for it.

    `business_info` is for the public demo (main.py, POST /demo/draft): free text the
    visitor pasted (policies, prices, FAQ). When it is not None (even "") the profile
    runs without tools, with the text as its only reference besides the email."""
    chosen = PROFILES[profile]
    demo = business_info is not None
    if demo:
        chosen = replace(chosen, prompt=chosen.prompt + DEMO_SECTION, tools=[])
    with tracing.email_trace(email, profile, model, demo=demo) as traced:
        draft = await _draft(email, chosen, model, trace, business_info)
        traced.finish(draft)
        return draft


def reference_block(business_info: str) -> str:
    """The pasted text, fenced like the email: data, not instructions. A closing tag
    inside it is defused, so the text can't end the block early."""
    safe = re.sub(
        r"</\s*business_information\s*>", "[/business_information]", business_info, flags=re.I
    )
    return f"<business_information>\n{safe or '(none provided)'}\n</business_information>\n\n"


async def _draft(
    email: Email,
    profile: Profile,
    model: str,
    trace: list | None,
    business_info: str | None = None,
):
    thread = (
        f"Earlier messages in this thread (oldest first):\n{email.thread}\n---\n"
        if email.thread
        else ""
    )
    messages = [
        {
            "role": "user",
            "content": (
                f"{reference_block(business_info) if business_info is not None else ''}"
                f"<email>\n"
                f"Sender: {email.sender}\n"
                f"Subject: {email.subject}\n"
                f"{thread}"
                f"Body (the new message to reply to): {email.body}\n"
                f"</email>"
            ),
        }
    ]
    max_tokens = 16000 if business_info is None else DEMO_MAX_TOKENS
    response = await call_claude(messages, profile, model, max_tokens)

    rounds = 0
    while response.stop_reason == "tool_use":
        if rounds == MAX_TOOL_ROUNDS:
            raise DraftError(
                f"Claude still asks for tools after {MAX_TOOL_ROUNDS} rounds: "
                "stopped without a draft"
            )
        rounds += 1
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                with tracing.tool_span(block.name, block.input) as record:
                    text = run_tool(block.name, block.input, email)
                    record(text)
                if trace is not None:
                    trace.append(
                        {"tool": block.name, "input": block.input, "result": text}
                    )
                tool_results += [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                    }
                ]
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ]
        response = await call_claude(messages, profile, model, max_tokens)

    # No text block when the answer was cut (max_tokens) or Claude refused
    text = next(
        (block.text for block in response.content if block.type == "text"), None
    )
    if text is None:
        raise DraftError(
            f"Claude's answer has no text (stop_reason: {response.stop_reason}): "
            "there is no draft to read"
        )
    draft = Draft.model_validate_json(text)
    if not draft.needs_reply:
        # Fixed text written here, not by Claude: nothing to invent, nothing to send
        draft.draft = NO_REPLY_NOTE.format(reason=draft.internal_notes)
    return draft
