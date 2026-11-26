"""Shared test setup."""

import os

import pytest


@pytest.fixture(autouse=True)
def no_langfuse_in_the_environment(monkeypatch):
    """The tests never see Langfuse variables from the shell or a .env file, so
    they can't trace to (or authenticate against) a real server by accident."""
    for name in list(os.environ):
        if name.startswith("LANGFUSE_"):
            monkeypatch.delenv(name)
    monkeypatch.delenv("TRACELOOP_TRACE_CONTENT", raising=False)
