"""
Claude writes the draft reply for an email.

Uses the async client, the system prompt and tools of the chosen profile
(profiles.py) and a structured output, so the result always matches `Draft`.
While Claude asks for tools, they run (tools.py) and the results go back to it.
"""

import anthropic

from agent.models import Draft, Email
from agent.profiles import PROFILES, Profile
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


class DraftError(Exception):
    """Claude didn't produce a draft for this email (the API itself answered fine)."""


async def call_claude(messages, profile: Profile, model: str):
    # The API doesn't accept tools=[]: if the profile has no tools, the parameter is left out
    extra = {"tools": profile.tools} if profile.tools else {}
    return await client.messages.create(
        max_tokens=16000,
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
    email: Email, profile: str = "store", model: str = MODEL, trace: list | None = None
) -> Draft:
    """Drafts the reply to one email. If `trace` is a list, every tool call is appended
    to it as {"tool", "input", "result"}: the evals use it to check what Claude did
    instead of taking its word for it."""
    profile = PROFILES[profile]
    thread = (
        f"Earlier messages in this thread (oldest first):\n{email.thread}\n---\n"
        if email.thread
        else ""
    )
    messages = [
        {
            "role": "user",
            "content": (
                f"<email>\n"
                f"Sender: {email.sender}\n"
                f"Subject: {email.subject}\n"
                f"{thread}"
                f"Body (the new message to reply to): {email.body}\n"
                f"</email>"
            ),
        }
    ]
    response = await call_claude(messages, profile, model)

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
                text = run_tool(block.name, block.input, email)
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
        response = await call_claude(messages, profile, model)

    # No text block when the answer was cut (max_tokens) or Claude refused
    text = next(
        (block.text for block in response.content if block.type == "text"), None
    )
    if text is None:
        raise DraftError(
            f"Claude's answer has no text (stop_reason: {response.stop_reason}): "
            "there is no draft to read"
        )
    return Draft.model_validate_json(text)
