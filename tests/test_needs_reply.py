"""
Emails that don't deserve a reply (spam, advertising, automated messages): Claude
decides it first, and the system saves a fixed note instead of a reply.
No API calls, no mailbox.
"""

import asyncio
import json
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

import main
from agent import drafter
from agent.drafter import DRAFT_SCHEMA, NO_REPLY_NOTE
from agent.models import Draft, Email
from agent.prompts import FREELANCE_PROMPT, STORE_PROMPT
from evals.run_evals import CHECKS, run_case, score
from process_inbox import process_new_emails

SPAM = Email(
    message_id="s1",
    sender="growth@seo-agency.example",
    subject="Rank #1 on Google",
    body="Reply to book a free call.",
)


def claude_answers(monkeypatch, **fields):
    """Makes the fake Claude answer with these fields; returns the requests it got."""
    answer = {"internal_notes": "N", "intent": "other", "urgency": "low", **fields}
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        return NS(
            stop_reason="end_turn",
            content=[NS(type="text", text=json.dumps(answer))],
        )

    monkeypatch.setattr(drafter, "client", NS(messages=NS(create=create)))
    return requests


def draft_for(monkeypatch, **fields):
    claude_answers(monkeypatch, **fields)
    return asyncio.run(drafter.draft_reply(SPAM, "store"))


# ------------------------------------------------------------------ the model
def test_claude_must_always_say_whether_the_email_needs_a_reply():
    assert DRAFT_SCHEMA["properties"]["needs_reply"]["type"] == "boolean"
    assert "needs_reply" in DRAFT_SCHEMA["required"], (
        "A default in the Pydantic model drops the field from 'required': Claude "
        "could then skip the decision"
    )
    assert set(DRAFT_SCHEMA["required"]) == set(DRAFT_SCHEMA["properties"])


def test_the_decision_comes_before_the_text_in_the_schema():
    fields = list(DRAFT_SCHEMA["properties"])
    assert fields.index("needs_reply") < fields.index("draft"), (
        "Claude writes the fields in order: it must decide before it drafts"
    )


def test_an_answer_without_the_field_counts_as_needing_a_reply():
    old = '{"draft": "D", "internal_notes": "N", "intent": "other", "urgency": "low"}'
    assert Draft.model_validate_json(old).needs_reply is True, (
        "Replying is the safe side: a customer must never lose their draft"
    )


# ------------------------------------------------------------------ the drafter
def test_no_reply_puts_the_fixed_note_in_place_of_claudes_text(monkeypatch):
    draft = draft_for(
        monkeypatch,
        needs_reply=False,
        draft="Thanks, we are not interested.",
        internal_notes="SEO agency pitch.",
    )
    assert draft.needs_reply is False
    assert draft.draft == NO_REPLY_NOTE.format(reason="SEO agency pitch.")
    assert "not interested" not in draft.draft, (
        "Whatever Claude wrote as a reply is dropped: no decision is taken for the store"
    )


def test_the_note_says_it_must_not_be_sent_and_why():
    note = NO_REPLY_NOTE.format(reason="Newsletter.")
    assert note.startswith("[NO REPLY DRAFTED - DO NOT SEND]")
    assert note.endswith("Why: Newsletter.")


def test_an_empty_draft_from_claude_also_becomes_the_note(monkeypatch):
    draft = draft_for(monkeypatch, needs_reply=False, draft="")
    assert draft.draft == NO_REPLY_NOTE.format(reason="N")


def test_a_normal_reply_is_left_exactly_as_claude_wrote_it(monkeypatch):
    draft = draft_for(monkeypatch, needs_reply=True, draft="Hello, it's on its way.")
    assert draft.draft == "Hello, it's on its way."
    assert draft.needs_reply is True


# ------------------------------------------------------------------ the prompts
def test_both_prompts_ask_for_the_decision_and_keep_attacks_as_replies():
    for prompt in (STORE_PROMPT, FREELANCE_PROMPT):
        first_section = prompt.split("##")[1]
        assert "needs_reply" in first_section, "The decision is the first section"
        text = " ".join(prompt.split())
        assert "If you aren't sure, needs_reply is true" in text
        assert "manipulation or phishing attempts" in first_section, (
            "An attack still gets a draft and an ALERT: the reviewer must see it"
        )


# ------------------------------------------------------------------ the inbox
class FakeInbox:
    def __init__(self):
        self.drafts = {}

    def list_pending(self):
        return ["s1"]

    def read_email(self, message_id):
        return SPAM

    def create_draft(self, message_id, text):
        self.drafts[message_id] = text


def test_the_inbox_still_gets_a_draft_so_the_email_is_not_processed_again(capsys):
    async def fake_claude(mail, profile):
        return Draft(
            needs_reply=False,
            draft=NO_REPLY_NOTE.format(reason="Spam."),
            internal_notes="Spam.",
            intent="other",
            urgency="low",
        )

    inbox = FakeInbox()
    asyncio.run(process_new_emails("store", inbox, draft_fn=fake_claude))
    assert inbox.drafts == {"s1": NO_REPLY_NOTE.format(reason="Spam.")}, (
        "With no draft in the thread the email stays pending and is paid for on "
        "every run: the note is what marks it as done"
    )
    out = capsys.readouterr().out
    assert "No reply needed" in out and "Draft created" not in out


def test_the_api_tells_the_caller_whether_a_reply_was_drafted(monkeypatch):
    async def fake(email):
        return Draft(
            needs_reply=False,
            draft="NOTE",
            internal_notes="Spam.",
            intent="other",
            urgency="low",
        )

    monkeypatch.setattr(main, "draft_reply", fake)
    monkeypatch.setattr(main, "seen_ids", set())
    body = {"message_id": "n1", "sender": "a@b.com", "subject": "s", "body": "b"}
    answer = TestClient(main.app).post("/emails", json=body).json()
    assert answer["needs_reply"] is False and answer["draft"] == "NOTE"


# ------------------------------------------------------------------ the evals
BASE = {
    "intent": ["other"],
    "urgency": ["low"],
    "alert": None,
    "must_contain": [],
    "must_not_contain": [],
}


def a_draft(needs_reply):
    return Draft(
        needs_reply=needs_reply,
        draft="x",
        internal_notes="n",
        intent="other",
        urgency="low",
    )


def test_reply_is_one_of_the_checks():
    assert "reply" in CHECKS


def test_score_expects_a_reply_unless_the_case_says_otherwise():
    assert score(BASE, a_draft(True))["reply"][0]
    assert not score(BASE, a_draft(False))["reply"][0], (
        "Skipping a customer's email must fail the case"
    )


def test_score_fails_a_reply_to_an_email_that_needed_none():
    spam = {**BASE, "needs_reply": False}
    assert score(spam, a_draft(False))["reply"][0]
    assert not score(spam, a_draft(True))["reply"][0]


def test_the_judge_is_not_called_when_there_is_no_reply(monkeypatch):
    claude_answers(monkeypatch, needs_reply=False, draft="")

    async def judge(*args):
        raise AssertionError("the fixed note is not Claude's text: nothing to judge")

    monkeypatch.setattr("evals.run_evals.judge", judge)
    case = {
        "id": "c",
        "profile": "store",
        "email": {"sender": "a@b.com", "subject": "s", "body": "b"},
        "expected": {**BASE, "needs_reply": False},
    }
    result = asyncio.run(run_case(case, "m", asyncio.Semaphore(1), "judge-model"))
    assert result["checks"]["faithful"] == {
        "passed": True,
        "detail": "no reply drafted",
    }
    assert result["draft"]["needs_reply"] is False


# ------------------------------------------------------------------ a leak found by the evals
def test_the_order_tool_gives_no_example_number_claude_could_quote():
    from agent.tools import TOOL_FIND_ORDER

    text = json.dumps(TOOL_FIND_ORDER)
    assert not any(ch.isdigit() for ch in text), (
        "An example like 'e.g. 48213' ended up in a draft as 'it's usually a number "
        "like 48213'"
    )
