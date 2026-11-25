"""
Checks the judge before trusting it: runs it on drafts whose verdict is known.

judge_cases.json has hand-written drafts for a few emails, half of them faithful
to the evidence and half with a claim that isn't in it (an invented price, a
promised reply time, a refund taken from an injected instruction). If the judge
doesn't separate them, its verdicts on the real drafts mean nothing.

Run it with (paid API calls, one per case):
    uv run --env-file .env python -m evals.check_judge
"""

import argparse
import asyncio
import json
from pathlib import Path

from evals.judge import JUDGE_MODEL, judge

EVALS_DIR = Path(__file__).parent


async def main(model):
    cases = json.loads((EVALS_DIR / "judge_cases.json").read_text())
    verdicts = await asyncio.gather(
        *(judge(c["email"], c["tool_calls"], c["draft"], model) for c in cases)
    )

    print(f"Judge: {model} | Cases: {len(cases)}\n")
    agree = caught = missed = false_alarms = 0
    results = []
    for case, verdict in zip(cases, verdicts):
        ok = verdict["faithful"] == case["faithful"]
        agree += ok
        if not case["faithful"]:
            caught += ok
            missed += not ok
        elif not ok:
            false_alarms += 1
        expected = "faithful" if case["faithful"] else "UNFAITHFUL"
        print(
            f"{'✅' if ok else '❌'} {case['id']:34} expected {expected:10} {verdict['unsupported_claims']}"
        )
        results.append(
            {"id": case["id"], "expected_faithful": case["faithful"], **verdict}
        )

    unfaithful = sum(1 for c in cases if not c["faithful"])
    summary = {
        "agreement": f"{agree}/{len(cases)}",
        "unfaithful_caught": f"{caught}/{unfaithful}",
        "false_alarms": f"{false_alarms}/{len(cases) - unfaithful}",
    }
    print(f"\nSummary: {summary}")
    out = EVALS_DIR / "results" / "judge_check.json"
    out.write_text(
        json.dumps(
            {"judge": model, "summary": summary, "results": results},
            indent=1,
            ensure_ascii=False,
        )
    )
    print(f"Saved: {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=JUDGE_MODEL)
    asyncio.run(main(parser.parse_args().model))
