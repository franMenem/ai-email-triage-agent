"""
Retrieval evals: does the FAQ search find the right document?

Each case in retrieval_cases.json is a query and the id of the document that
answers it, or null when no FAQ document does. No Claude calls: this only runs
the embedding model, so it's free and takes a few seconds.

The cases come in two groups:
- dev: the ones looked at while choosing rag.MIN_SCORE and rag.TOP_K
- test: written afterwards and never used to choose anything. A change only
  counts as an improvement if it also holds here

The numbers per group, with the settings the tool uses:
- found: of the queries that have an answer, how many get their document back
- top 1: of those same queries, how many get it in first place
- only_right: of those same queries, how many get that document and nothing else
- precision: of all the documents returned for them, how many are the right one
- rejected: of the queries with no answer, how many get no documents at all.
  Split in two, because they behave in opposite ways: rejected_none (nothing in
  the store answers it) and rejected_policy (a store policy does, so the right
  tool is find_policy, not this search)

It also prints the same numbers for other minimum scores, to show what each
choice costs.

Run it with:
    uv run --group rag python -m evals.run_retrieval_evals
"""

import json
from pathlib import Path

from agent import rag

EVALS_DIR = Path(__file__).parent
SPLITS = ["dev", "test"]
MIN_SCORES = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]


def retrieve(ranked, min_score, k):
    """The ids search_docs would return, from a list already scored and sorted."""
    return [doc_id for doc_id, score in ranked if score >= min_score][:k]


def measure(scored_cases, min_score, k):
    """Counts found / top 1 / rejected for one choice of min_score and k."""
    with_answer = [c for c in scored_cases if c["expected"] is not None]
    without_answer = [c for c in scored_cases if c["expected"] is None]
    found = top_1 = only_right = returned = rejected = 0
    for case in with_answer:
        ids = retrieve(case["ranked"], min_score, k)
        found += case["expected"] in ids
        top_1 += ids[:1] == [case["expected"]]
        only_right += ids == [case["expected"]]
        returned += len(ids)
    by_kind = {"none": [0, 0], "policy": [0, 0]}
    for case in without_answer:
        empty = retrieve(case["ranked"], min_score, k) == []
        rejected += empty
        by_kind[case["kind"]][0] += empty
        by_kind[case["kind"]][1] += 1
    return {
        "found": f"{found}/{len(with_answer)}",
        "top_1": f"{top_1}/{len(with_answer)}",
        "only_right": f"{only_right}/{len(with_answer)}",
        "precision": f"{found}/{returned}",
        "rejected": f"{rejected}/{len(without_answer)}",
        "rejected_none": "{}/{}".format(*by_kind["none"]),
        "rejected_policy": "{}/{}".format(*by_kind["policy"]),
    }


def main():
    cases = json.loads((EVALS_DIR / "retrieval_cases.json").read_text())
    index = rag.faq_index()
    scored_cases = [
        {**case, "ranked": rag.score_docs(case["query"], index)} for case in cases
    ]
    by_split = {
        split: [c for c in scored_cases if c["split"] == split] for split in SPLITS
    }

    print(f"Cases: {len(cases)} | min_score={rag.MIN_SCORE} k={rag.TOP_K}\n")
    print(f"{'':3} {'':5} {'expected':16} {'returned':34} query")
    for case in scored_cases:
        ids = retrieve(case["ranked"], rag.MIN_SCORE, rag.TOP_K)
        expected = case["expected"]
        ok = ids == [] if expected is None else expected in ids
        best_id, best_score = case["ranked"][0]
        returned = ", ".join(ids) if ids else f"(none; best {best_id} {best_score:.2f})"
        print(
            f"{'✅' if ok else '❌':3} {case['split']:5} {expected or '(no answer)':16} "
            f"{returned:34} {case['query']}"
        )

    summary = {
        split: measure(by_split[split], rag.MIN_SCORE, rag.TOP_K) for split in SPLITS
    }
    print()
    for split in SPLITS:
        print(f"Summary ({split}): {summary[split]}")

    print("\nSame minimum score, other maximum counts (found, precision):")
    for k in [1, 2, 3]:
        rows = {split: measure(by_split[split], rag.MIN_SCORE, k) for split in SPLITS}
        print(
            f"k={k}  dev {rows['dev']['found']:>6} {rows['dev']['precision']:>6}"
            f"   test {rows['test']['found']:>6} {rows['test']['precision']:>6}"
        )

    print("\nWhat other minimum scores would cost (same k):")
    print(f"{'':9}  {'dev':^17}  {'test':^17}")
    print(
        f"{'min_score':>9}  {'found':>7}  {'rejected':>8}  {'found':>7}  {'rejected':>8}"
    )
    sweep = {}
    for min_score in MIN_SCORES:
        rows = {
            split: measure(by_split[split], min_score, rag.TOP_K) for split in SPLITS
        }
        sweep[str(min_score)] = rows
        mark = "  <- current" if min_score == rag.MIN_SCORE else ""
        print(
            f"{min_score:>9}  {rows['dev']['found']:>7}  {rows['dev']['rejected']:>8}  "
            f"{rows['test']['found']:>7}  {rows['test']['rejected']:>8}{mark}"
        )

    out = EVALS_DIR / "results" / "retrieval.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "min_score": rag.MIN_SCORE,
                "k": rag.TOP_K,
                "summary": summary,
                "sweep": sweep,
                "cases": [
                    {
                        "query": c["query"],
                        "split": c["split"],
                        "expected": c["expected"],
                        "returned": retrieve(c["ranked"], rag.MIN_SCORE, rag.TOP_K),
                        "best": [c["ranked"][0][0], round(c["ranked"][0][1], 3)],
                    }
                    for c in scored_cases
                ],
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    print(f"\nSaved: {out.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    main()
