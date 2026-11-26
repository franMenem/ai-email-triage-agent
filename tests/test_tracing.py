"""
Langfuse tracing (agent/tracing.py): off without keys, content hidden by default,
content visible when asked, the right observation types, eval scores.
No network and no real keys: the Langfuse client is a fake that records what it gets.
"""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

import langfuse
from agent import drafter, tracing
from agent.models import Draft, Email
from langfuse.types import OtelSpanPatch

SECRET = "ZEBRAMARKER123"
EMAIL = Email(
    message_id="m1",
    sender="ana@example.com",
    subject=f"Order {SECRET}",
    body=f"Where is my order? {SECRET}",
)
DRAFT = Draft(
    draft=f"Hi Ana, it ships soon. {SECRET}",
    internal_notes="n",
    intent="order_status",
    urgency="low",
)


class FakeObservation:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.updates = []
        self.scores = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def score_trace(self, **kwargs):
        self.scores.append(kwargs)


class FakeClient:
    def __init__(self):
        self.observations = []
        self.flushed = 0

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        observation = FakeObservation(kwargs)
        self.observations.append(observation)
        yield observation

    def flush(self):
        self.flushed += 1


@pytest.fixture(autouse=True)
def clean_tracing(monkeypatch):
    """Every test starts with tracing off and leaves no global state behind."""
    monkeypatch.setattr(tracing, "_client", None)
    monkeypatch.setattr(tracing, "_capture_content", False)
    # conftest.py already cleared the env vars; monkeypatch puts back what setup() writes


@pytest.fixture
def fake_client(monkeypatch):
    """Tracing on with a fake client; propagate_attributes records its arguments."""
    client = FakeClient()
    client.propagated = []

    @contextmanager
    def propagate_attributes(**kwargs):
        client.propagated.append(kwargs)
        yield

    monkeypatch.setattr(tracing, "_client", client)
    monkeypatch.setattr(langfuse, "propagate_attributes", propagate_attributes)
    return client


def on(monkeypatch, capture_content):
    monkeypatch.setattr(tracing, "_capture_content", capture_content)


# ------------------------------------------------------------------ off
def test_setup_without_keys_does_nothing():
    assert tracing.setup() is False
    assert tracing.setup(capture_content=True) is False
    assert not tracing.is_on()
    assert "TRACELOOP_TRACE_CONTENT" not in os.environ


def test_setup_with_only_one_key_stays_off(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    assert tracing.setup() is False


def test_without_keys_langfuse_is_never_imported():
    # A fresh interpreter: this test process may already have imported langfuse
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LANGFUSE_", "TRACELOOP_"))
    }
    code = (
        "import sys\n"
        "from agent import tracing\n"
        "assert tracing.setup() is False\n"
        "with tracing.email_trace(None, 'store', 'm') as t:\n"
        "    t.finish(None)\n"
        "with tracing.tool_span('search_docs', {}) as record:\n"
        "    record('x')\n"
        "with tracing.eval_case('c', 'm') as case:\n"
        "    case.score('check', True, 'd')\n"
        "tracing.flush()\n"
        "assert 'langfuse' not in sys.modules\n"
        "assert 'opentelemetry.instrumentation.anthropic' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_everything_is_a_no_op_when_off():
    with tracing.email_trace(EMAIL, "store", "m") as traced:
        traced.finish(DRAFT)
    with tracing.tool_span("find_order", {"a": 1}) as record:
        record("result")
    with tracing.eval_case("c", "m") as case:
        case.score("reply", False, "detail")
    tracing.flush()


def test_draft_reply_works_the_same_with_tracing_off(monkeypatch):
    answer = {
        "draft": "Hello",
        "internal_notes": "n",
        "intent": "other",
        "urgency": "low",
        "needs_reply": True,
    }

    async def create(**kwargs):
        return NS(
            stop_reason="end_turn",
            content=[NS(type="text", text=json.dumps(answer))],
        )

    monkeypatch.setattr(drafter, "client", NS(messages=NS(create=create)))
    assert not tracing.is_on()
    draft = asyncio.run(drafter.draft_reply(EMAIL, "store"))
    assert draft.draft == "Hello"
    assert draft.intent == "other"


# ------------------------------------------------------------------ _strip_content
def strip(attributes):
    span = NS(attributes=attributes)
    params = NS(spans={"id": span})
    result = tracing._strip_content(params=params)
    return result.span_patches


def test_strip_content_deletes_every_content_attribute_and_keeps_the_rest():
    attributes = {
        "gen_ai.prompt.0.content": SECRET,
        "gen_ai.completion.0.content": SECRET,
        "gen_ai.input.messages": SECRET,
        "gen_ai.output.messages": SECRET,
        "gen_ai.system_instructions": SECRET,
        "gen_ai.tool.call.arguments": SECRET,
        "gen_ai.tool.call.result": SECRET,
        "langfuse.observation.input": SECRET,
        "langfuse.observation.output": SECRET,
        "langfuse.trace.input": SECRET,
        "langfuse.trace.output": SECRET,
        "gen_ai.request.model": "claude-haiku-5-5",
        "gen_ai.usage.input_tokens": 10,
        "langfuse.observation.type": "generation",
        "langfuse.observation.metadata.intent": "other",
    }
    patch = strip(attributes)["id"]
    assert isinstance(patch, OtelSpanPatch)
    deleted = set(patch.delete_attributes)
    assert deleted == {k for k, v in attributes.items() if v == SECRET}
    assert not deleted & {
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "langfuse.observation.type",
        "langfuse.observation.metadata.intent",
    }


def test_strip_content_leaves_spans_without_content_alone():
    assert strip({"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 3}) == {}


def test_every_content_prefix_is_covered():
    for prefix in tracing.CONTENT_PREFIXES:
        assert tracing._is_content(prefix + ".0.content")
    assert not tracing._is_content("gen_ai.request.model")


# ------------------------------------------------------------------ email_trace
def test_content_is_hidden_by_default(fake_client):
    with tracing.email_trace(EMAIL, "store", "claude-haiku-5-5") as traced:
        traced.finish(DRAFT)
    (root,) = fake_client.observations
    everything = json.dumps([root.kwargs, root.updates, fake_client.propagated])
    assert SECRET not in everything
    assert EMAIL.sender not in everything
    assert root.kwargs["input"] == tracing.HIDDEN
    assert root.updates[0]["output"] == tracing.HIDDEN


def test_labels_are_sent_even_when_content_is_hidden(fake_client):
    with tracing.email_trace(EMAIL, "store", "claude-haiku-5-5") as traced:
        traced.finish(DRAFT)
    (root,) = fake_client.observations
    assert root.kwargs["name"] == "draft-email"
    assert root.kwargs["as_type"] == "agent"
    assert root.updates[0]["metadata"] == {
        "needs_reply": True,
        "intent": "order_status",
        "urgency": "low",
    }
    attributes = fake_client.propagated[0]
    assert attributes["tags"] == ["profile:store"]
    assert attributes["metadata"] == {"profile": "store", "draft_model": "claude-haiku-5-5"}


def test_content_is_visible_with_capture_content(monkeypatch, fake_client):
    on(monkeypatch, True)
    thread_email = EMAIL.model_copy(update={"thread": "earlier message"})
    with tracing.email_trace(thread_email, "store", "m") as traced:
        traced.finish(DRAFT)
    (root,) = fake_client.observations
    assert root.kwargs["input"] == {
        "sender": EMAIL.sender,
        "subject": EMAIL.subject,
        "body": EMAIL.body,
        "thread": "earlier message",
    }
    assert root.updates[0]["output"] == DRAFT.model_dump()


def test_the_trace_gets_its_name_unless_one_already_exists(fake_client):
    with tracing.email_trace(EMAIL, "store", "m"):
        pass
    assert fake_client.propagated[0]["trace_name"] == "draft-email"


def test_inside_an_eval_case_the_trace_keeps_its_name(fake_client, monkeypatch):
    monkeypatch.setattr(tracing, "_inside_a_trace", lambda: True)
    with tracing.email_trace(EMAIL, "store", "m"):
        pass
    assert fake_client.propagated[0]["trace_name"] is None


# ------------------------------------------------------------------ tool_span
def test_search_docs_is_a_retriever_and_the_other_tools_are_tools(fake_client):
    for name in ("search_docs", "find_order", "find_policy"):
        with tracing.tool_span(name, {"q": "x"}) as record:
            record("result")
    types = {o.kwargs["name"]: o.kwargs["as_type"] for o in fake_client.observations}
    assert types == {
        "search_docs": "retriever",
        "find_order": "tool",
        "find_policy": "tool",
    }


def test_tool_span_accepts_an_explicit_type(fake_client):
    with tracing.tool_span("judge", "draft", as_type="evaluator"):
        pass
    assert fake_client.observations[0].kwargs["as_type"] == "evaluator"


def test_tool_input_and_result_are_hidden_by_default(fake_client):
    with tracing.tool_span("find_order", {"email": SECRET}) as record:
        record(f"order of {SECRET}")
    (observation,) = fake_client.observations
    assert SECRET not in json.dumps([observation.kwargs, observation.updates])
    assert observation.kwargs["input"] == tracing.HIDDEN
    assert observation.updates == [{"output": tracing.HIDDEN}]


def test_tool_input_and_result_are_visible_with_capture_content(
    monkeypatch, fake_client
):
    on(monkeypatch, True)
    with tracing.tool_span("find_order", {"email": "a"}) as record:
        record("order 1")
    (observation,) = fake_client.observations
    assert observation.kwargs["input"] == {"email": "a"}
    assert observation.updates == [{"output": "order 1"}]


# ------------------------------------------------------------------ eval_case
def test_eval_case_score_records_the_check(fake_client):
    with tracing.eval_case("store-spam", "claude-haiku-5-5") as case:
        case.score("reply", True, "needs_reply=False")
        case.score("tools", False, "called=[]")
    (root,) = fake_client.observations
    assert root.kwargs["name"] == "eval-case"
    assert root.kwargs["as_type"] == "chain"
    assert root.kwargs["input"] == {"case": "store-spam"}
    assert root.scores == [
        dict(name="reply_ok", value=1, data_type="BOOLEAN", comment="needs_reply=False"),
        dict(name="tools_ok", value=0, data_type="BOOLEAN", comment="called=[]"),
    ]
    assert fake_client.propagated[0]["tags"] == ["eval"]
    assert fake_client.propagated[0]["metadata"] == {
        "case": "store-spam",
        "draft_model": "claude-haiku-5-5",
    }


def test_eval_case_gets_a_small_result_as_output(fake_client):
    with tracing.eval_case("store-spam", "m") as case:
        case.finish(False, ["tools"])
    (root,) = fake_client.observations
    assert root.updates == [{"output": {"passed": False, "failed_checks": ["tools"]}}]


def test_the_service_name_is_set_unless_the_host_chose_one(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setattr(tracing, "_client", None)
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    monkeypatch.setattr(langfuse, "Langfuse", lambda **kwargs: object())
    monkeypatch.setattr(AnthropicInstrumentor, "instrument", lambda self: None)
    try:
        assert tracing.setup() is True
        assert os.environ["OTEL_SERVICE_NAME"] == "ai-email-triage-agent"
    finally:
        monkeypatch.setattr(tracing, "_client", None)
        os.environ.pop("OTEL_SERVICE_NAME", None)
        os.environ.pop("TRACELOOP_TRACE_CONTENT", None)


# ------------------------------------------------------------------ flush
def test_flush_sends_what_is_queued(fake_client):
    tracing.flush()
    assert fake_client.flushed == 1
