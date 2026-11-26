"""
Runs the evals as a Langfuse experiment on the dataset made by langfuse_dataset.py.

It adds no checks of its own: the task is run_evals' own `_run_case` (the agent, the
seven checks, the judge) and the evaluators only turn its `checks` into scores. Each
dataset item gets one trace, linked to the item and to the run, with one score per
check named `<check>_ok` (1 = passed), like the traces of a plain eval run.

The run carries what is needed to compare it with another one: the model that writes
the drafts, the judge, a label for the prompt version, the prompt variant, a
fingerprint of the system prompts that really ran, and the git commit.
"""

import subprocess
from pathlib import Path

from evals.langfuse_dataset import DATASET_NAME, case_from_item, out_of_sync

REPO = Path(__file__).resolve().parent.parent


def git_state() -> dict:
    """Commit the code was at, and whether there were uncommitted changes."""

    def git(*args):
        out = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()

    try:
        return {
            "git_commit": git("rev-parse", "--short", "HEAD"),
            "git_dirty": "true" if git("status", "--porcelain") else "false",
        }
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}


def run_metadata(
    model, judge_model, prompt_version, prompt_variant, fingerprint, dataset=DATASET_NAME
) -> dict:
    """What Langfuse shows next to the run (all values are strings)."""
    return {
        "draft_model": model,
        "judge_model": judge_model or "none",
        "prompt_version": prompt_version,
        "prompt_variant": prompt_variant or "none (production prompts)",
        "prompt_fingerprint": fingerprint,
        "dataset": dataset,
        **git_state(),
    }


def make_task(run_case, model, judge_model):
    async def task(*, item, **kwargs):
        # The trace of the case: the Claude calls and the tools hang from this task
        return await run_case(case_from_item(item), model, judge_model)

    return task


def make_evaluators():
    from langfuse import Evaluation

    def check_scores(*, output, **kwargs):
        """One boolean score per check, plus whether the whole case passed."""
        if "error" in output:
            return [
                Evaluation(
                    name="error",
                    value=1,
                    data_type="BOOLEAN",
                    comment=output["error"],
                )
            ]
        checks = output["checks"]
        scores = [
            Evaluation(
                name=f"{name}_ok",
                value=1 if check["passed"] else 0,
                data_type="BOOLEAN",
                comment=check["detail"],
            )
            for name, check in checks.items()
        ]
        everything = all(check["passed"] for check in checks.values())
        scores.append(
            Evaluation(
                name="all_checks_ok", value=1 if everything else 0, data_type="BOOLEAN"
            )
        )
        return scores

    return [check_scores]


def make_run_evaluators(checks):
    from langfuse import Evaluation

    def pass_rates(*, item_results, **kwargs):
        """The share of cases that passed each check: the number to compare runs by."""
        rates = []
        for name in [f"{c}_ok" for c in checks] + ["all_checks_ok"]:
            values = [
                evaluation.value
                for result in item_results
                for evaluation in result.evaluations
                if evaluation.name == name
            ]
            if values:
                rates.append(
                    Evaluation(
                        name=f"{name}_rate",
                        value=sum(values) / len(values),
                        comment=f"{sum(values)}/{len(values)} cases",
                    )
                )
        return rates

    return [pass_rates]


def run_experiment(
    client,
    run_case,
    checks,
    cases,
    run_name,
    model,
    judge_model,
    concurrency,
    metadata,
    dataset_name=DATASET_NAME,
):
    """Runs every item of the dataset. Returns the ExperimentResult; the results of
    the cases are the `output` of each of its item_results."""
    dataset = client.get_dataset(dataset_name)
    stale = out_of_sync(dataset, cases)
    if stale:
        # Running old inputs and calling the result "the evals" would be misleading
        raise SystemExit(
            f"ABORT: the dataset {dataset_name!r} differs from cases.json in "
            f"{len(stale)} case(s) ({stale[:3]}...). Run: python -m evals.langfuse_dataset"
        )
    return dataset.run_experiment(
        name=run_name,
        run_name=run_name,
        description=(
            f"{model} writes, {judge_model or 'no judge'} judges. "
            f"Prompt: {metadata['prompt_version']}. Code: {metadata['git_commit']}"
        ),
        task=make_task(run_case, model, judge_model),
        evaluators=make_evaluators(),
        run_evaluators=make_run_evaluators(checks),
        max_concurrency=concurrency,
        metadata=metadata,
    )
