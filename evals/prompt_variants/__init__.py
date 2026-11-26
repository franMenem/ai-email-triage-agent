"""
Prompt variants: try another system prompt in the evals without touching production.

A variant is a file in this folder, `<name>.py`, that defines one dict:

    PROMPTS = {"store": "<the whole system prompt for the store profile>"}

Only the profiles listed there change; the others keep their production prompt.
Pick it with `python -m evals.run_evals --prompt-variant <name>`. See `_example.py`.

How it stays out of production: the eval runner swaps the profile table that
agent/drafter.py reads for the duration of the run (`applied`) and puts it back
afterwards. agent/profiles.py and agent/prompts.py are never modified, and neither
main.py nor process_inbox.py know this folder exists.
"""

import hashlib
import importlib.util
import json
import re
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from agent import drafter
from agent.profiles import PROFILES

VARIANTS_DIR = Path(__file__).parent
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _read(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location(f"prompt_variant_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prompts = getattr(module, "PROMPTS", None)
    if not isinstance(prompts, dict) or not prompts:
        raise ValueError(f"{path.name}: it must define PROMPTS = {{profile: prompt}}")
    for profile, prompt in prompts.items():
        if profile not in PROFILES:
            raise ValueError(
                f"{path.name}: unknown profile {profile!r} (known: {sorted(PROFILES)})"
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{path.name}: the prompt of {profile!r} is empty")
    return dict(prompts)


def load_variant(name: str, directory: Path = VARIANTS_DIR) -> dict:
    """The {profile: prompt} overrides of the variant called `name`."""
    if not NAME_RE.match(name):
        raise ValueError(
            f"Invalid variant name {name!r}: lowercase letters, digits, - and _ "
            "(files starting with _ are examples, copy them to a new name)"
        )
    path = Path(directory) / f"{name}.py"
    if not path.is_file():
        available = sorted(
            p.stem for p in Path(directory).glob("*.py") if NAME_RE.match(p.stem)
        )
        raise ValueError(f"No variant {name!r} in {directory} (available: {available})")
    return _read(path)


@contextmanager
def applied(overrides: dict | None):
    """Inside the `with`, drafter.py answers with the overridden prompts.

    The production table (agent.profiles.PROFILES) is not touched: drafter gets a
    copy with the replaced profiles and the original comes back on exit."""
    if not overrides:
        yield
        return
    original = drafter.PROFILES
    drafter.PROFILES = {
        name: replace(profile, prompt=overrides[name]) if name in overrides else profile
        for name, profile in original.items()
    }
    try:
        yield
    finally:
        drafter.PROFILES = original


def prompts_fingerprint() -> str:
    """Short hash of the system prompts drafter.py would use right now: two runs with
    the same label but another fingerprint did not run the same prompts."""
    prompts = {name: profile.prompt for name, profile in sorted(drafter.PROFILES.items())}
    return hashlib.sha256(json.dumps(prompts, sort_keys=True).encode()).hexdigest()[:10]
