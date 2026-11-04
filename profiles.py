"""
Agent profiles: each one defines which prompt it answers with and which tools it can use.

To add a new use case, add a profile here; drafter.py doesn't change.
"""

from dataclasses import dataclass, field

from prompts import FREELANCE_PROMPT, STORE_PROMPT
from tools import TOOL_FIND_ORDER, TOOL_FIND_POLICY


@dataclass(frozen=True)
class Profile:
    name: str
    prompt: str
    tools: list = field(default_factory=list)


PROFILES = {
    # Demo: customer support for an online store, with orders and policies in the database
    "store": Profile("store", STORE_PROMPT, [TOOL_FIND_ORDER, TOOL_FIND_POLICY]),
    # Daily use: replies to Fran's clients, no tools (yet)
    "freelance": Profile("freelance", FREELANCE_PROMPT),
}
