"""
Langfuse dataset and experiments for the evals (evals/langfuse_*.py, prompt_variants).
No network and no real keys: the Langfuse client and the dataset are fakes.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent import drafter, profiles
from evals import langfuse_dataset, langfuse_experiment, langfuse_guard, prompt_variants
from evals.langfuse_dataset import case_from_item, case_to_item, sync_dataset

REPO = Path(__file__).resolve().parent.parent
CASES = langfuse_dataset.load_cases()


def clean_env(**extra):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LANGFUSE_", "TRACELOOP_"))
    }
    return {**env, "ANTHROPIC_API_KEY": "not-a-real-key", **extra}


# ------------------------------------------------------------ case -> item
def test_item_id_is_the_case_id_and_carries_input_and_expectations():
    case = next(c for c in CASES if c["id"] == "store-order-status")
    item = case_to_item(case)
    assert item["id"] == "store-order-status"
    assert item["input"] == {"profile": "store", "email": case["email"]}
    assert item["expected_output"] == case["expected"]
    assert item["metadata"]["profile"] == "store"
    assert item["metadata"]["tools_expected"] == ["find_order"]


def test_every_case_survives_the_round_trip():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) == 35
    for case in CASES:
        assert case_from_item(case_to_item(case)) == case
        # A Langfuse DatasetItem has attributes, not keys
        as_object = NS(**case_to_item(case))
        assert case_from_item(as_object) == case


def test_kinds_tell_the_cases_apart():
    kinds = {c["id"]: case_to_item(c)["metadata"]["kind"] for c in CASES}
    assert kinds["store-spam"] == "no_reply"
    assert kinds["store-injection-dump-db"] == "attack"
    assert kinds["freelance-thread-confirm"] == "thread"
    assert kinds["store-faq-wholesale"] == "faq"
    assert kinds["store-order-status"] == "standard"


# ---------------------------------------------------------------- the sync
class FakeDatasetClient:
    """Keeps items the way Langfuse does: by id, upserting."""

    def __init__(self):
        self.datasets = {}
        self.writes = 0

    def get_dataset(self, name):
        if name not in self.datasets:
            error = Exception("not found")
            error.status_code = 404
            raise error
        return NS(items=list(self.datasets[name].values()))

    def create_dataset(self, *, name, description=None, metadata=None):
        self.datasets.setdefault(name, {})

    def create_dataset_item(
        self, *, dataset_name, id, input, expected_output, metadata, status
    ):
        self.writes += 1
        self.datasets[dataset_name][id] = NS(
            id=id,
            input=json.loads(json.dumps(input)),
            expected_output=json.loads(json.dumps(expected_output)),
            metadata=json.loads(json.dumps(metadata)),
            status=status,
        )


def test_first_sync_creates_every_item_with_the_case_id():
    client = FakeDatasetClient()
    stats = sync_dataset(client, CASES)
    assert stats == {"created": 35, "updated": 0, "unchanged": 0, "archived": 0}
    assert set(client.datasets[langfuse_dataset.DATASET_NAME]) == {c["id"] for c in CASES}


def test_syncing_twice_does_not_duplicate_or_rewrite():
    client = FakeDatasetClient()
    sync_dataset(client, CASES)
    writes = client.writes
    stats = sync_dataset(client, CASES)
    assert stats == {"created": 0, "updated": 0, "unchanged": 35, "archived": 0}
    assert client.writes == writes
    assert len(client.datasets[langfuse_dataset.DATASET_NAME]) == 35


def test_an_edited_case_updates_its_item_and_nothing_else():
    client = FakeDatasetClient()
    sync_dataset(client, CASES)
    edited = json.loads(json.dumps(CASES))
    edited[0]["expected"]["must_contain"].append("something new")
    stats = sync_dataset(client, edited)
    assert stats == {"created": 0, "updated": 1, "unchanged": 34, "archived": 0}
    stored = client.datasets[langfuse_dataset.DATASET_NAME][edited[0]["id"]]
    assert "something new" in stored.expected_output["must_contain"]


def test_a_removed_case_is_archived_not_deleted():
    client = FakeDatasetClient()
    sync_dataset(client, CASES)
    stats = sync_dataset(client, CASES[1:])
    assert stats["archived"] == 1 and stats["created"] == 0
    assert client.datasets[langfuse_dataset.DATASET_NAME][CASES[0]["id"]].status == "ARCHIVED"
    # and a second sync doesn't archive it again
    assert sync_dataset(client, CASES[1:])["archived"] == 0


def test_repeated_case_ids_are_refused():
    with pytest.raises(ValueError):
        sync_dataset(FakeDatasetClient(), CASES + CASES[:1])


def test_out_of_sync_lists_missing_and_changed_cases():
    client = FakeDatasetClient()
    sync_dataset(client, CASES[:-1])
    dataset = client.get_dataset(langfuse_dataset.DATASET_NAME)
    assert langfuse_dataset.out_of_sync(dataset, CASES) == [CASES[-1]["id"]]


# --------------------------------------------------- without keys / the host
def test_without_keys_nothing_imports_or_touches_langfuse():
    code = (
        "import sys\n"
        "import evals.run_evals, evals.langfuse_dataset, evals.langfuse_experiment\n"
        "import evals.langfuse_guard, evals.prompt_variants\n"
        "assert not evals.langfuse_guard.has_keys()\n"
        "assert 'langfuse' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=clean_env(), cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_an_experiment_without_keys_stops_before_calling_claude():
    result = subprocess.run(
        [sys.executable, "-m", "evals.run_evals", "--langfuse-experiment", "x"],
        env=clean_env(),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert "needs LANGFUSE_PUBLIC_KEY" in result.stderr


def test_variant_flags_need_an_experiment():
    result = subprocess.run(
        [sys.executable, "-m", "evals.run_evals", "--prompt-variant", "x"],
        env=clean_env(),
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert "need --langfuse-experiment" in result.stderr


def test_the_guard_refuses_a_missing_or_foreign_host(monkeypatch):
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    with pytest.raises(SystemExit):
        langfuse_guard.check_environment()
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    with pytest.raises(SystemExit):
        langfuse_guard.check_environment()
    monkeypatch.setenv("LANGFUSE_BASE_URL", langfuse_guard.EXPECTED_HOST)
    langfuse_guard.check_environment()
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example.com")
    with pytest.raises(SystemExit):
        langfuse_guard.check_environment()
    monkeypatch.setenv("LANGFUSE_HOST", langfuse_guard.EXPECTED_HOST + "/")
    langfuse_guard.check_environment()


def test_the_guard_checks_the_host_the_client_resolved():
    assert langfuse_guard.check_client(NS(_base_url=langfuse_guard.EXPECTED_HOST))
    with pytest.raises(SystemExit):
        langfuse_guard.check_client(NS(_base_url="https://cloud.langfuse.com"))
    with pytest.raises(SystemExit):
        langfuse_guard.check_client(NS())


def test_make_client_aborts_on_a_wrong_host_before_creating_anything(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    import langfuse

    def boom(*args, **kwargs):
        raise AssertionError("the client must not be created")

    monkeypatch.setattr(langfuse, "Langfuse", boom)
    with pytest.raises(SystemExit):
        langfuse_dataset.make_client()


# --------------------------------------------------------- prompt variants
@pytest.fixture
def variants_dir(tmp_path):
    (tmp_path / "terse.py").write_text(
        'PROMPTS = {"store": "You are terse. <email> ALERT:"}\n'
    )
    (tmp_path / "broken.py").write_text('PROMPTS = {"nobody": "x"}\n')
    (tmp_path / "empty.py").write_text('PROMPTS = {"store": "  "}\n')
    return tmp_path


def test_a_variant_gives_the_prompt_per_profile(variants_dir):
    overrides = prompt_variants.load_variant("terse", variants_dir)
    assert overrides == {"store": "You are terse. <email> ALERT:"}


def test_bad_variants_are_refused(variants_dir):
    for name in ["broken", "empty", "missing", "_example", "../terse", "Terse"]:
        with pytest.raises(ValueError):
            prompt_variants.load_variant(name, variants_dir)


def test_the_example_is_valid_but_cannot_be_picked_by_name():
    overrides = prompt_variants._read(prompt_variants.VARIANTS_DIR / "_example.py")
    assert set(overrides) == {"store"}
    assert overrides["store"].startswith(profiles.PROFILES["store"].prompt)
    with pytest.raises(ValueError):
        prompt_variants.load_variant("_example")


def test_the_override_applies_only_inside_the_evals_and_production_is_untouched():
    production = {name: p.prompt for name, p in profiles.PROFILES.items()}
    before = prompt_variants.prompts_fingerprint()
    with prompt_variants.applied({"store": "OVERRIDE"}):
        assert drafter.PROFILES["store"].prompt == "OVERRIDE"
        # The other profiles, and the tools of the changed one, stay as they were
        assert drafter.PROFILES["freelance"] is profiles.PROFILES["freelance"]
        assert drafter.PROFILES["store"].tools == profiles.PROFILES["store"].tools
        # The production table itself is never modified
        assert {n: p.prompt for n, p in profiles.PROFILES.items()} == production
        assert prompt_variants.prompts_fingerprint() != before
    assert drafter.PROFILES is profiles.PROFILES
    assert prompt_variants.prompts_fingerprint() == before


def test_the_override_is_restored_even_if_the_run_fails():
    with pytest.raises(RuntimeError):
        with prompt_variants.applied({"store": "OVERRIDE"}):
            raise RuntimeError("the run crashed")
    assert drafter.PROFILES is profiles.PROFILES


def test_the_drafter_really_sends_the_overridden_prompt(monkeypatch):
    seen = []

    async def fake_call(messages, profile, model, max_tokens=16000):
        seen.append(profile.prompt)
        raise RuntimeError("stop here")

    monkeypatch.setattr(drafter, "call_claude", fake_call)
    email = drafter.Email(message_id="m", sender="a@example.com", subject="s", body="b")

    async def draft():
        with pytest.raises(RuntimeError):
            await drafter.draft_reply(email, "store", "m")

    with prompt_variants.applied({"store": "OVERRIDE"}):
        asyncio.run(draft())
    asyncio.run(draft())
    assert seen == ["OVERRIDE", profiles.PROFILES["store"].prompt]


def test_no_overrides_changes_nothing():
    with prompt_variants.applied(None):
        assert drafter.PROFILES is profiles.PROFILES


# -------------------------------------------------------- the experiment run
def test_run_metadata_has_what_is_needed_to_compare_runs(monkeypatch):
    monkeypatch.setattr(
        langfuse_experiment, "git_state", lambda: {"git_commit": "abc1234", "git_dirty": "false"}
    )
    metadata = langfuse_experiment.run_metadata(
        "claude-haiku-5-5", "claude-sonnet-5-5", "baseline", None, "f1f1f1"
    )
    assert metadata == {
        "draft_model": "claude-haiku-5-5",
        "judge_model": "claude-sonnet-5-5",
        "prompt_version": "baseline",
        "prompt_variant": "none (production prompts)",
        "prompt_fingerprint": "f1f1f1",
        "dataset": "email-triage-cases",
        "git_commit": "abc1234",
        "git_dirty": "false",
    }
    assert all(isinstance(v, str) for v in metadata.values())
    no_judge = langfuse_experiment.run_metadata("m", None, "v", "terse", "f")
    assert no_judge["judge_model"] == "none" and no_judge["prompt_variant"] == "terse"


def test_git_state_in_this_repo_gives_a_commit():
    state = langfuse_experiment.git_state()
    assert state["git_dirty"] in {"true", "false"}
    assert len(state["git_commit"]) >= 7


def result(**checks):
    return {
        "id": "c",
        "checks": {name: {"passed": ok, "detail": f"d-{name}"} for name, ok in checks.items()},
    }


def test_the_evaluators_turn_the_checks_into_one_score_each():
    (evaluator,) = langfuse_experiment.make_evaluators()
    scores = evaluator(output=result(reply=True, intent=False))
    assert [(s.name, s.value, s.data_type, s.comment) for s in scores] == [
        ("reply_ok", 1, "BOOLEAN", "d-reply"),
        ("intent_ok", 0, "BOOLEAN", "d-intent"),
        ("all_checks_ok", 0, "BOOLEAN", None),
    ]
    assert evaluator(output=result(reply=True))[-1].value == 1


def test_an_errored_case_is_one_error_score_not_a_pass():
    (evaluator,) = langfuse_experiment.make_evaluators()
    (score,) = evaluator(output={"id": "c", "error": "boom"})
    assert (score.name, score.value, score.comment) == ("error", 1, "boom")


def test_run_level_pass_rates():
    (evaluator,) = langfuse_experiment.make_run_evaluators(["reply", "intent"])
    (run_evaluate,) = langfuse_experiment.make_evaluators()
    outputs = [result(reply=True, intent=True), result(reply=True, intent=False)]
    item_results = [NS(evaluations=run_evaluate(output=o)) for o in outputs]
    rates = {r.name: (r.value, r.comment) for r in evaluator(item_results=item_results)}
    assert rates == {
        "reply_ok_rate": (1.0, "2/2 cases"),
        "intent_ok_rate": (0.5, "1/2 cases"),
        "all_checks_ok_rate": (0.5, "1/2 cases"),
    }


class FakeExperimentDataset:
    def __init__(self, items):
        self.items = items
        self.kwargs = None

    def run_experiment(self, **kwargs):
        self.kwargs = kwargs
        return "result"


def synced_dataset(cases=CASES):
    client = FakeDatasetClient()
    sync_dataset(client, cases)
    dataset = FakeExperimentDataset(
        list(client.datasets[langfuse_dataset.DATASET_NAME].values())
    )
    return NS(get_dataset=lambda name: dataset), dataset


def test_the_run_is_named_and_tagged_and_the_task_runs_the_existing_case_logic():
    client, dataset = synced_dataset()
    metadata = {"prompt_version": "baseline", "git_commit": "abc1234"}
    calls = []

    async def run_case(case, model, judge_model):
        calls.append((case, model, judge_model))
        return {"id": case["id"], "checks": {}}

    out = langfuse_experiment.run_experiment(
        client, run_case, ["reply"], CASES, "haiku-baseline-2026-11-26",
        "claude-haiku-5-5", "claude-sonnet-5-5", 3, metadata,
    )
    assert out == "result"
    kwargs = dataset.kwargs
    assert kwargs["name"] == kwargs["run_name"] == "haiku-baseline-2026-11-26"
    assert kwargs["metadata"] == metadata
    assert kwargs["max_concurrency"] == 3
    assert len(kwargs["evaluators"]) == 1 and len(kwargs["run_evaluators"]) == 1

    item = dataset.items[4]
    output = asyncio.run(kwargs["task"](item=item))
    assert output == {"id": item.id, "checks": {}}
    # The task gets exactly the case from cases.json, rebuilt from the dataset item
    assert calls == [(next(c for c in CASES if c["id"] == item.id), "claude-haiku-5-5", "claude-sonnet-5-5")]


def test_an_outdated_dataset_stops_the_experiment():
    client, dataset = synced_dataset(CASES[:-1])
    with pytest.raises(SystemExit):
        langfuse_experiment.run_experiment(
            client, None, [], CASES, "run", "m", None, 1, {"prompt_version": "v", "git_commit": "x"}
        )
    assert dataset.kwargs is None


def test_the_no_unbacked_extras_variant_only_adds_lines_to_the_production_prompts():
    overrides = prompt_variants.load_variant("no_unbacked_extras")
    assert set(overrides) == {"store", "freelance"}
    for name, prompt in overrides.items():
        production = profiles.PROFILES[name].prompt.splitlines()
        added = [line for line in prompt.splitlines() if line not in production]
        assert len(added) == 2
        assert [line for line in production if line not in prompt.splitlines()] == []
