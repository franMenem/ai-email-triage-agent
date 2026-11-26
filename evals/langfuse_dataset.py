"""
Keeps a Langfuse dataset in sync with evals/cases.json.

cases.json stays the source of truth (it is reviewed in git, next to the code). The
dataset is a copy that Langfuse can run experiments on, so runs of different prompt
versions can be compared side by side in its UI.

Each case becomes one dataset item:
- id: the case id, so syncing twice updates the item instead of duplicating it
- input: {"profile", "email"}, what the agent receives
- expected_output: the `expected` block of the case, what the checks look for
- metadata: profile, kind of case, tools expected (to filter in the UI)

Run it with (talks to Langfuse only; no Claude calls, so it is free):
    uv run --env-file .env python -m evals.langfuse_dataset --check-host   # prints the host and stops
    uv run --env-file .env python -m evals.langfuse_dataset                # syncs

Items that are in the dataset but no longer in cases.json are archived, not deleted,
so old experiment runs keep pointing at them.
"""

import argparse
import json
from pathlib import Path

from evals import langfuse_guard

DATASET_NAME = "email-triage-cases"
CASES_FILE = Path(__file__).parent / "cases.json"
DESCRIPTION = (
    "Made-up emails for the email triage agent, with what a good draft looks like. "
    "Synced from evals/cases.json: edit the file, not the dataset."
)


def load_cases(path: Path = CASES_FILE) -> list:
    return json.loads(Path(path).read_text())


def case_kind(case) -> str:
    """A label to filter the dataset by: what kind of email this case is."""
    expected = case["expected"]
    if expected.get("needs_reply", True) is False:
        return "no_reply"
    if expected["alert"] is True:
        return "attack"
    if case["email"].get("thread"):
        return "thread"
    if "search_docs" in expected.get("must_call", []):
        return "faq"
    return "standard"


def case_to_item(case) -> dict:
    """The dataset item (as keyword arguments of create_dataset_item) for one case."""
    return {
        "id": case["id"],
        "input": {"profile": case["profile"], "email": case["email"]},
        "expected_output": case["expected"],
        "metadata": {
            "profile": case["profile"],
            "kind": case_kind(case),
            "tools_expected": case["expected"].get("must_call", []),
            "source": "evals/cases.json",
        },
    }


def case_from_item(item) -> dict:
    """The inverse of case_to_item: the case dict that run_evals works with.
    `item` is a Langfuse DatasetItem (attributes) or the dict from case_to_item."""
    get = item.get if isinstance(item, dict) else lambda key: getattr(item, key)
    value = get("input")
    return {
        "id": get("id"),
        "profile": value["profile"],
        "email": value["email"],
        "expected": get("expected_output"),
    }


def _same(a, b) -> bool:
    # Compared as JSON so that key order doesn't matter
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _is_up_to_date(wanted: dict, existing) -> bool:
    return (
        _same(wanted["input"], existing.input)
        and _same(wanted["expected_output"], existing.expected_output)
        and _same(wanted["metadata"], existing.metadata)
        and str(getattr(existing.status, "value", existing.status)) == "ACTIVE"
    )


def _get_dataset(client, name):
    try:
        return client.get_dataset(name)
    except Exception as e:
        if getattr(e, "status_code", None) == 404:
            return None
        raise


def out_of_sync(dataset, cases) -> list:
    """Ids of the cases that the dataset doesn't have, or has with other content."""
    existing = {item.id: item for item in dataset.items}
    return [
        case["id"]
        for case in cases
        if case["id"] not in existing
        or not _is_up_to_date(case_to_item(case), existing[case["id"]])
    ]


def sync_dataset(client, cases, dataset_name: str = DATASET_NAME) -> dict:
    """Makes the dataset match `cases`. Safe to run any number of times: items that
    already match are left alone. Returns how many items were created, updated,
    left unchanged and archived."""
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("cases.json has repeated ids")

    dataset = _get_dataset(client, dataset_name)
    if dataset is None:
        client.create_dataset(
            name=dataset_name,
            description=DESCRIPTION,
            metadata={"source": "evals/cases.json"},
        )
        existing = {}
    else:
        existing = {item.id: item for item in dataset.items}

    stats = {"created": 0, "updated": 0, "unchanged": 0, "archived": 0}
    for case in cases:
        wanted = case_to_item(case)
        current = existing.get(wanted["id"])
        if current is None:
            stats["created"] += 1
        elif _is_up_to_date(wanted, current):
            stats["unchanged"] += 1
            continue
        else:
            stats["updated"] += 1
        client.create_dataset_item(dataset_name=dataset_name, status="ACTIVE", **wanted)

    for item_id, current in existing.items():
        still_active = str(getattr(current.status, "value", current.status)) == "ACTIVE"
        if item_id not in ids and still_active:
            client.create_dataset_item(
                dataset_name=dataset_name,
                id=item_id,
                input=current.input,
                expected_output=current.expected_output,
                metadata=current.metadata,
                status="ARCHIVED",
            )
            stats["archived"] += 1
    return stats


def make_client():
    """The Langfuse client, created only after the host check (see langfuse_guard)."""
    langfuse_guard.check_environment()
    if not langfuse_guard.has_keys():
        raise SystemExit("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set")
    from langfuse import Langfuse

    client = Langfuse(environment="evals")
    langfuse_guard.check_client(client)
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument(
        "--check-host",
        action="store_true",
        help="only create the client, print the host it resolved and stop",
    )
    args = parser.parse_args()
    client = make_client()
    if args.check_host:
        return
    try:
        stats = sync_dataset(client, load_cases(), args.dataset)
        print(f"Dataset {args.dataset!r}: {stats}")
    finally:
        client.flush()


if __name__ == "__main__":
    main()
