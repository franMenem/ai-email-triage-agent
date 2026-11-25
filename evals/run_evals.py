"""
Evals: runs the agent on a fixed set of test emails and scores each draft.

Each case in cases.json says what a good answer looks like:
- intent / urgency: the accepted values
- alert: whether internal_notes must start with "ALERT:" (null = not checked)
- must_contain / must_not_contain: text the draft must or must not include
  (e.g. another customer's product, tool names, made-up prices)
- must_contain_any (optional): groups of alternatives; the draft needs at least
  one of each group. For facts that only a tool supplies and can be worded in
  several ways, like a delivery date
- must_call / must_not_call (optional): tools Claude must or must not have
  called. Checked against the calls the drafter recorded, not against what the
  model says it did

With --judge, a second model also reads each draft next to the email and the
tool results, and lists the claims that aren't backed by them (see judge.py).

Run it with (uses the Claude API: each case is a paid call, two with --judge):
    uv run --group rag --env-file .env python -m evals.run_evals
    uv run --group rag --env-file .env python -m evals.run_evals --judge
    uv run --group rag --env-file .env python -m evals.run_evals --model claude-sonnet-5-5

The detailed result is saved in evals/results/<model>.json.
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from agent.drafter import MODEL, draft_reply
from agent.models import Email
from evals.judge import JUDGE_MODEL, judge

EVALS_DIR = Path(__file__).parent
CHECKS = ["intent", "urgency", "alert", "content", "tools"]


def score(expected, draft, tool_calls=()):
    """Returns {check: (passed, detail)} for one draft."""
    text = draft.draft.lower()
    missing = [s for s in expected["must_contain"] if s.lower() not in text]
    forbidden = [s for s in expected["must_not_contain"] if s.lower() in text]
    missing_any = [
        group
        for group in expected.get("must_contain_any", [])
        if not any(s.lower() in text for s in group)
    ]
    has_alert = draft.internal_notes.strip().startswith("ALERT:")
    called = {call["tool"] for call in tool_calls}
    not_called = [t for t in expected.get("must_call", []) if t not in called]
    wrongly_called = [t for t in expected.get("must_not_call", []) if t in called]
    content_ok = not missing and not forbidden and not missing_any
    tools_ok = not not_called and not wrongly_called
    return {
        "intent": (draft.intent in expected["intent"], draft.intent),
        "urgency": (draft.urgency in expected["urgency"], draft.urgency),
        "alert": (
            expected["alert"] is None or has_alert == expected["alert"],
            f"alert={has_alert}",
        ),
        "content": (
            content_ok,
            "ok"
            if content_ok
            else f"missing={missing} forbidden={forbidden} none_of={missing_any}",
        ),
        "tools": (
            tools_ok,
            f"called={sorted(called)}"
            if tools_ok
            else f"not called={not_called} wrongly called={wrongly_called}",
        ),
    }


async def run_case(case, model, semaphore, judge_model):
    async with semaphore:  # limits how many calls run at the same time
        email = Email(message_id=case["id"], **case["email"])
        tool_calls = []
        start = time.perf_counter()
        try:
            draft = await draft_reply(email, case["profile"], model, trace=tool_calls)
        except Exception as e:
            return {"id": case["id"], "error": repr(e), "tool_calls": tool_calls}
        seconds = round(time.perf_counter() - start, 1)
        checks = score(case["expected"], draft, tool_calls)
        if judge_model:
            try:
                verdict = await judge(
                    case["email"], tool_calls, draft.draft, judge_model
                )
                checks["faithful"] = (
                    verdict["faithful"],
                    f"unsupported={verdict['unsupported_claims']}",
                )
            except Exception as e:
                checks["faithful"] = (False, f"judge error: {e!r}")
        return {
            "id": case["id"],
            "seconds": seconds,
            "checks": {
                name: {"passed": ok, "detail": detail}
                for name, (ok, detail) in checks.items()
            },
            "tool_calls": tool_calls,
            "draft": draft.model_dump(),
        }


async def main(model, concurrency, judge_model):
    cases = json.loads((EVALS_DIR / "cases.json").read_text())
    checks = CHECKS + (["faithful"] if judge_model else [])
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(
        *(run_case(c, model, semaphore, judge_model) for c in cases)
    )

    judged = f" | Judge: {judge_model}" if judge_model else ""
    print(f"Model: {model} | Cases: {len(cases)}{judged}\n")
    print(f"{'case':36} " + " ".join(f"{c:8}" for c in checks) + "  secs")
    totals = {c: 0 for c in checks}
    for r in results:
        if "error" in r:
            print(f"{r['id']:36} ERROR {r['error']}")
            continue
        marks = []
        for c in checks:
            ok = r["checks"][c]["passed"]
            totals[c] += ok
            marks.append(f"{'✅' if ok else '❌':8}")
        print(f"{r['id']:36} " + " ".join(marks) + f"  {r['seconds']}")

    print("\nFailures:")
    for r in results:
        for c, check in r.get("checks", {}).items():
            if not check["passed"]:
                print(f"- {r['id']} [{c}]: {check['detail']}")

    ran = [r for r in results if "checks" in r]
    errors = len(results) - len(ran)
    summary = {c: f"{totals[c]}/{len(ran)}" for c in checks}
    perfect = sum(1 for r in ran if all(v["passed"] for v in r["checks"].values()))
    times = [r["seconds"] for r in ran]
    median = statistics.median(times) if times else 0.0
    print(
        f"\nSummary: {summary} | all checks passed: {perfect}/{len(ran)}"
        f" | errors: {errors} | median secs: {median:.1f}"
    )

    out = EVALS_DIR / "results" / f"{model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "model": model,
                "judge": judge_model,
                "summary": summary,
                "perfect": perfect,
                "errors": errors,
                "median_seconds": median,
                "results": results,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    print(f"Saved: {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--judge",
        nargs="?",
        const=JUDGE_MODEL,
        default=None,
        metavar="MODEL",
        help=f"also judge each draft for unsupported claims (default judge: {JUDGE_MODEL})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.model, args.concurrency, args.judge))
