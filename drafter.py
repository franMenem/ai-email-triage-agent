"""
Claude writes the draft reply for an email.

Uses the async client, the system prompt and tools of the chosen profile
(profiles.py) and a structured output, so the result always matches `Draft`.
While Claude asks for tools, they run (tools.py) and the results go back to it.
"""

import anthropic

from models import Draft, Email
from profiles import PROFILES, Profile
from tools import run_tool

# Created once per module; the API key comes from ANTHROPIC_API_KEY
client = anthropic.AsyncAnthropic()

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string"},
        "internal_notes": {"type": "string"},
        "intent": {
            "type": "string",
            "enum": [
                "order_status",
                "return_request",
                "complaint",
                "general_inquiry",
                "other",
            ],
        },
        "urgency": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["draft", "internal_notes", "intent", "urgency"],
    "additionalProperties": False,
}


async def call_claude(messages, profile: Profile):
    # The API doesn't accept tools=[]: if the profile has no tools, the parameter is left out
    extra = {"tools": profile.tools} if profile.tools else {}
    return await client.messages.create(
        max_tokens=16000,
        model="claude-opus-5-5",
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


async def draft_reply(email: Email, profile: str = "store") -> Draft:
    profile = PROFILES[profile]
    messages = [
        {
            "role": "user",
            "content": (
                f"<email>\n"
                f"Sender: {email.sender}\n"
                f"Subject: {email.subject}\n"
                f"Body: {email.body}\n"
                f"</email>"
            ),
        }
    ]
    response = await call_claude(messages, profile)

    while response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                text = run_tool(block.name, block.input, email)
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
        response = await call_claude(messages, profile)

    return Draft.model_validate_json(
        next(block for block in response.content if block.type == "text").text
    )
