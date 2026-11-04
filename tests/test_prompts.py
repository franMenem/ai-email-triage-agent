"""Prompts and profiles: what the code relies on being there. No API calls."""

from profiles import PROFILES
from prompts import FREELANCE_PROMPT, STORE_PROMPT


def test_prompts_explain_the_untrusted_email_block():
    for prompt in [STORE_PROMPT, FREELANCE_PROMPT]:
        assert isinstance(prompt, str), "Must be a string, not a list"
        assert "<email>" in prompt
        assert "ALERT:" in prompt


def test_store_prompt_mentions_its_tools():
    tool_names = [t["name"] for t in PROFILES["store"].tools]
    for name in tool_names:
        assert name in STORE_PROMPT, f"The prompt must say when to use {name}"
