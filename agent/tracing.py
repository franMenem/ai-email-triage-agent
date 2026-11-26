"""
Optional tracing with Langfuse: one trace per email, with every Claude request
(model, tokens, cost, time) and every tool call nested inside it.

Two rules shape this file:

- It is off unless setup() is called AND the Langfuse keys are in the environment.
  Without them every function here does nothing, so the tests, the evals and the
  deploy work the same with or without Langfuse.
- The content of an email is private. By default only numbers and labels leave the
  server (model, tokens, time, intent, urgency, which tools ran). The text of the
  email, the sender, what the tools returned and the draft are sent only when
  capture_content is on, which the evals do because their emails are made up.

The rest of the code never imports langfuse: it calls email_trace(), tool_span()
and eval_case() from here.
"""

import os
from contextlib import contextmanager

HIDDEN = "[hidden]"
# Name the traces show as their service (otherwise "unknown_service:python3")
SERVICE_NAME = "ai-email-triage-agent"
# Read by the Anthropic instrumentation each time it records a request
_CONTENT_SWITCH = "TRACELOOP_TRACE_CONTENT"
# Attributes that carry text of the email, the tools or the draft. With content
# off they are deleted right before export, whoever wrote them: a second lock in
# case the switch above stops working in a future version of the instrumentation.
CONTENT_PREFIXES = (
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.trace.input",
    "langfuse.trace.output",
)
# The retriever needs the model; the other tools query the database
TOOL_TYPES = {"search_docs": "retriever"}

_client = None
_capture_content = False


def is_on() -> bool:
    return _client is not None


def _is_content(key: str) -> bool:
    return key.startswith(CONTENT_PREFIXES)


def _strip_content(*, params):
    """mask_otel_spans hook: deletes every content attribute of the batch."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        found = tuple(key for key in span.attributes if _is_content(key))
        if found:
            patches[identifier] = OtelSpanPatch(delete_attributes=found)
    return MaskOtelSpansResult(span_patches=patches)


def setup(capture_content: bool = False, environment: str = "production") -> bool:
    """Turns tracing on if the Langfuse keys are set. Returns whether it is on.

    capture_content=False (the default) is for real mailboxes: no text leaves.
    """
    global _client, _capture_content
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return False
    if _client is not None:
        return True

    # Imported here: without the keys, langfuse is never loaded
    from langfuse import Langfuse
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    _capture_content = capture_content
    # The OpenTelemetry SDK that Langfuse uses reads the service name from this variable
    # (a value already set by the host wins)
    os.environ.setdefault("OTEL_SERVICE_NAME", SERVICE_NAME)
    os.environ[_CONTENT_SWITCH] = "true" if capture_content else "false"
    _client = Langfuse(
        environment=environment,
        mask_otel_spans=None if capture_content else _strip_content,
    )
    # Records every client.messages.create as a generation: model, tokens, time
    AnthropicInstrumentor().instrument()
    return True


def client():
    """The Langfuse client, or None when tracing is off (the evals run experiments with it)."""
    return _client


def _visible(value):
    return value if _capture_content else HIDDEN


class _NoTrace:
    """Stands in for a trace when tracing is off."""

    def finish(self, draft):
        pass


class _EmailTrace:
    def __init__(self, observation):
        self._observation = observation

    def finish(self, draft):
        """Records how the email was classified and, if allowed, the draft."""
        self._observation.update(
            output=_visible(draft.model_dump()),
            metadata={
                "needs_reply": draft.needs_reply,
                "intent": draft.intent,
                "urgency": draft.urgency,
            },
        )


def _inside_a_trace() -> bool:
    from opentelemetry import trace

    return trace.get_current_span().get_span_context().is_valid


@contextmanager
def email_trace(email, profile: str, model: str, demo: bool = False):
    """One trace for one email: everything inside the `with` hangs from it."""
    if _client is None:
        yield _NoTrace()
        return
    from langfuse import propagate_attributes

    shown = {"sender": email.sender, "subject": email.subject, "body": email.body}
    if email.thread:
        shown["thread"] = email.thread
    with propagate_attributes(
        # Inside an eval case the trace already has a name: keep that one
        trace_name=None if _inside_a_trace() else "draft-email",
        tags=[f"profile:{profile}"] + (["demo"] if demo else []),
        metadata={"profile": profile, "draft_model": model},
    ):
        with _client.start_as_current_observation(
            name="draft-email", as_type="agent", input=_visible(shown)
        ) as observation:
            yield _EmailTrace(observation)


@contextmanager
def tool_span(name: str, tool_input, as_type: str | None = None):
    """One observation per tool call. Yields a function to record the result."""
    if _client is None:
        yield lambda result: None
        return
    with _client.start_as_current_observation(
        name=name,
        as_type=as_type or TOOL_TYPES.get(name, "tool"),
        input=_visible(tool_input),
    ) as observation:
        yield lambda result: observation.update(output=_visible(result))


class _NoCase:
    def score(self, check, passed, detail):
        pass

    def finish(self, passed, failed_checks):
        pass


class _EvalCase:
    def __init__(self, observation):
        self._observation = observation

    def score(self, check: str, passed: bool, detail: str):
        """Attaches one eval check to the trace, so runs can be filtered by it."""
        # "<check>_ok": the score says the check passed, not that something went off
        # (a score called "alert: True" looks like an alert was raised)
        self._observation.score_trace(
            name=f"{check}_ok",
            value=1 if passed else 0,
            data_type="BOOLEAN",
            comment=detail,
        )

    def finish(self, passed: bool, failed_checks: list):
        """The result of the case. Eval data, not email content: always visible."""
        self._observation.update(
            output={"passed": passed, "failed_checks": failed_checks}
        )


@contextmanager
def eval_case(case_id: str, model: str):
    """One trace for one eval case: the draft and the judge hang from it."""
    if _client is None:
        yield _NoCase()
        return
    from langfuse import propagate_attributes

    with propagate_attributes(
        trace_name="eval-case",
        tags=["eval"],
        metadata={"case": case_id, "draft_model": model},
    ):
        with _client.start_as_current_observation(
            name="eval-case", as_type="chain", input={"case": case_id}
        ) as observation:
            yield _EvalCase(observation)


def flush():
    """Sends what is still queued. Scripts must call it before they exit."""
    if _client is not None:
        _client.flush()
