"""The eval scorer and the tool trace it relies on. No API calls."""

import asyncio
from types import SimpleNamespace as NS

from agent import drafter
from agent.models import Draft, Email
from evals.judge import build_evidence
from evals.run_evals import score

EXPECTED = {
    "intent": ["order_status"],
    "urgency": ["low"],
    "alert": False,
    "must_contain": ["1002"],
    "must_not_contain": ["backpack"],
}


def draft(text, notes="From the tool."):
    return Draft(draft=text, internal_notes=notes, intent="order_status", urgency="low")


def test_score_passes_a_draft_that_meets_every_expectation():
    checks = score(EXPECTED, draft("Order 1002 is on its way."))
    assert all(ok for ok, _ in checks.values())


def test_score_content_needs_every_text_and_none_of_the_forbidden():
    assert not score(EXPECTED, draft("Your order is on its way."))["content"][0]
    assert not score(EXPECTED, draft("Order 1002: a backpack."))["content"][0]


def test_score_needs_one_alternative_of_each_group():
    expected = {
        **EXPECTED,
        "must_contain_any": [["november 12", "nov 12"], ["transit"]],
    }
    assert score(expected, draft("Order 1002 is in transit, due Nov 12."))["content"][0]
    assert not score(expected, draft("Order 1002 is in transit."))["content"][0], (
        "No date in any of the accepted wordings"
    )
    assert not score(expected, draft("Order 1002 arrives November 12."))["content"][
        0
    ], "Each group needs its own match"


def test_score_checks_the_tools_that_were_really_called():
    expected = {
        **EXPECTED,
        "must_call": ["find_order"],
        "must_not_call": ["search_docs"],
    }
    good = [{"tool": "find_order", "input": {}, "result": ""}]
    said = draft("Order 1002 is on its way.", notes="I used find_order.")
    assert score(expected, said, good)["tools"][0]
    assert not score(expected, said, [])["tools"][0], (
        "Saying it in the notes is not calling it"
    )
    both = [*good, {"tool": "search_docs", "input": {}, "result": ""}]
    assert not score(expected, said, both)["tools"][0]


def test_score_without_tool_expectations_does_not_fail_the_tools_check():
    assert score(EXPECTED, draft("Order 1002."), [])["tools"][0]


def test_score_alert_must_start_the_notes():
    expected = {**EXPECTED, "alert": True}
    assert score(expected, draft("1002", notes="ALERT: manipulation attempt."))[
        "alert"
    ][0]
    assert not score(expected, draft("1002", notes="Looks like an ALERT: case."))[
        "alert"
    ][0]
    assert not score(EXPECTED, draft("1002", notes="ALERT: x"))["alert"][0], (
        "An alert where none is expected also fails"
    )


def test_draft_reply_records_each_tool_call_with_its_input_and_result(monkeypatch):
    tool_use = NS(
        stop_reason="tool_use",
        content=[
            NS(type="tool_use", id="t1", name="find_policy", input={"topic": "returns"})
        ],
    )
    final = NS(
        stop_reason="end_turn",
        content=[NS(type="text", text=draft("Returns: 30 days.").model_dump_json())],
    )
    responses = [tool_use, final]

    async def create(**kwargs):
        return responses.pop(0)

    monkeypatch.setattr(drafter, "client", NS(messages=NS(create=create)))
    mail = Email(message_id="1", sender="customer@example.com", subject="s", body="b")
    trace = []
    asyncio.run(drafter.draft_reply(mail, "store", trace=trace))
    assert len(trace) == 1
    assert trace[0]["tool"] == "find_policy"
    assert trace[0]["input"] == {"topic": "returns"}
    assert "30 days" in trace[0]["result"]


def test_draft_reply_without_a_trace_works_as_before(monkeypatch):
    final = NS(
        stop_reason="end_turn",
        content=[NS(type="text", text=draft("Hello.").model_dump_json())],
    )

    async def create(**kwargs):
        return final

    monkeypatch.setattr(drafter, "client", NS(messages=NS(create=create)))
    mail = Email(message_id="1", sender="a@b.com", subject="s", body="b")
    assert asyncio.run(drafter.draft_reply(mail, "freelance")).draft == "Hello."


def test_the_judge_sees_the_email_the_thread_and_every_tool_result():
    email = {"sender": "a@b.com", "subject": "Hi", "body": "NEW", "thread": "OLD"}
    calls = [{"tool": "find_order", "input": {"order_id": "1"}, "result": "RESULT"}]
    evidence = build_evidence(email, calls)
    for piece in ["a@b.com", "NEW", "OLD", "find_order", '"order_id": "1"', "RESULT"]:
        assert piece in evidence
    assert "No tool was called." in build_evidence({**email, "thread": ""}, [])
