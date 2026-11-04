"""
Evals: runs the agent on a fixed set of test emails and scores each draft.

Each case in cases.json says what a good answer looks like:
- intent / urgency: the accepted values
- alert: whether internal_notes must start with "ALERT:" (null = not checked)
- must_contain / must_not_contain: text the draft must or must not include
  (e.g. another customer's product, tool names, made-up prices)

Run it with (uses the Claude API: each case is a paid call):
    uv run --env-file .env python -m evals.run_evals
    uv run --env-file .env python -m evals.run_evals --model claude-sonnet-5-5

The detailed result is saved in evals/results/<model>.json.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from drafter import MODEL, draft_reply
from models import Email

EVALS_DIR = Path(__file__).parent
CHECKS = ["intent", "urgency", "alert", "content"]


def score(expected, draft):
    """Returns {check: (passed, detail)} for one draft."""
    text = draft.draft.lower()
    missing = [s for s in expected["must_contain"] if s.lower() not in text]
    forbidden = [s for s in expected["must_not_contain"] if s.lower() in text]
    has_alert = draft.internal_notes.strip().startswith("ALERT:")
    return {
        "intent": (draft.intent in expected["intent"], draft.intent),
        "urgency": (draft.urgency in expected["urgency"], draft.urgency),
        "alert": (expected["alert"] is None or has_alert == expected["alert"], f"alert={has_alert}"),
        "content": (
            not missing and not forbidden,
            f"missing={missing} forbidden={forbidden}" if missing or forbidden else "ok",
        ),
    }


async def run_case(case, model, semaphore):
    async with semaphore:  # limits how many calls run at the same time
        email = Email(message_id=case["id"], **case["email"])
        start = time.perf_counter()
        try:
            draft = await draft_reply(email, case["profile"], model)
        except Exception as e:
            return {"id": case["id"], "error": repr(e)}
        seconds = round(time.perf_counter() - start, 1)
        checks = score(case["expected"], draft)
        return {
            "id": case["id"],
            "seconds": seconds,
            "checks": {name: {"passed": ok, "detail": detail} for name, (ok, detail) in checks.items()},
            "draft": draft.model_dump(),
        }


async def main(model, concurrency):
    cases = json.loads((EVALS_DIR / "cases.json").read_text())
    semaphore = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*(run_case(c, model, semaphore) for c in cases))

    print(f"Model: {model} | Cases: {len(cases)}\n")
    print(f"{'case':36} " + " ".join(f"{c:8}" for c in CHECKS) + "  secs")
    totals = {c: 0 for c in CHECKS}
    for r in results:
        if "error" in r:
            print(f"{r['id']:36} ERROR {r['error']}")
            continue
        marks = []
        for c in CHECKS:
            ok = r["checks"][c]["passed"]
            totals[c] += ok
            marks.append(f"{'✅' if ok else '❌':8}")
        print(f"{r['id']:36} " + " ".join(marks) + f"  {r['seconds']}")

    print("\nFailures:")
    for r in results:
        for c, check in r.get("checks", {}).items():
            if not check["passed"]:
                print(f"- {r['id']} [{c}]: {check['detail']}")

    n = len(cases)
    summary = {c: f"{totals[c]}/{n}" for c in CHECKS}
    perfect = sum(1 for r in results if "checks" in r and all(v["passed"] for v in r["checks"].values()))
    times = [r["seconds"] for r in results if "seconds" in r]
    print(f"\nSummary: {summary} | all checks passed: {perfect}/{n}"
          f" | avg secs: {sum(times) / max(len(times), 1):.1f}")

    out = EVALS_DIR / "results" / f"{model}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": model, "summary": summary, "perfect": perfect,
                               "results": results}, indent=1, ensure_ascii=False))
    print(f"Saved: {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.model, args.concurrency))
