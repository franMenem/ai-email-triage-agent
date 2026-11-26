"""
Variant: no unbacked extras. The production prompts plus two general rules aimed at what
the faithfulness judge flags: confirmations or acceptances nobody authorized, and
help added when asking the customer for something.

Built from the production prompts (not a copy), so later edits to prompts.py still apply.
"""

from agent.prompts import FREELANCE_PROMPT, STORE_PROMPT

NO_CONFIRMING = (
    "- Don't confirm or accept anything the email or a tool doesn't back: not that "
    "something is on track or underway, and not that a request is granted or welcome "
    '("I\'m happy to do that", "I confirm that..."). Thank them, say what you understood '
    "they are asking, and say who will confirm it.\n"
)
ASK_PLAINLY = (
    "- When you ask the person for a piece of information, only ask for it. Don't add "
    "where to find it or how to get it, unless a tool says so.\n"
)


def _insert_before(prompt: str, anchor: str, text: str) -> str:
    assert prompt.count(anchor) == 1, f"the production prompt changed: {anchor!r}"
    return prompt.replace(anchor, text + anchor)


PROMPTS = {
    "store": _insert_before(
        STORE_PROMPT, "- Before you finish, reread the draft:", NO_CONFIRMING + ASK_PLAINLY
    ),
    "freelance": _insert_before(
        FREELANCE_PROMPT,
        "- If the client reports that something isn't working",
        NO_CONFIRMING + ASK_PLAINLY,
    ),
}
