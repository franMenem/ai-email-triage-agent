"""
Example of a prompt variant. It is NOT a real experiment and can't be selected by
name (files starting with _ are skipped): copy it to a new name, for example
`evals/prompt_variants/short-notes.py`, edit it and run

    uv run --group rag --env-file .env python -m evals.run_evals --judge \
        --prompt-variant short-notes --prompt-version short-notes ...

A variant gives the WHOLE system prompt of each profile it changes. Building it from the
production prompt keeps the diff small and means a later edit to prompts.py still applies.
"""

from agent.prompts import STORE_PROMPT

DESCRIPTION = "Example only: the production store prompt plus one extra line."

PROMPTS = {
    "store": STORE_PROMPT + "\n- Example extra rule: keep internal_notes under 60 words.\n",
}
