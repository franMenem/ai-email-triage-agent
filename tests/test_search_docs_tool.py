"""The search_docs tool: its definition, its executor and where it's registered.

The real rag.py loads an embedding model, so these tests swap it for a fake
module: no model, no API calls.
"""

import os
import subprocess
import sys
import types

import pytest

import agent
from agent import tools
from agent.models import Email
from agent.profiles import PROFILES
from agent.tools import TOOL_SEARCH_DOCS, run_tool

MAIL = Email(message_id="t", sender="customer@example.com", subject="", body="hi")
NOTHING_FOUND = "No FAQ document answers that question."


@pytest.fixture
def fake_rag(monkeypatch):
    """A stand-in for rag.py that records how search_docs is called."""
    fake = types.ModuleType("agent.rag")
    fake.FAQ_DOCS = {"hours": "Open 9 to 6.", "gifts": "Gift cards last 12 months."}
    fake.results = []
    fake.calls = []

    def search_docs(query, index, docs, **kwargs):
        fake.calls.append({"query": query, "index": index, "docs": docs, **kwargs})
        return fake.results

    fake.search_docs = search_docs
    fake.faq_index = lambda: "THE INDEX"
    monkeypatch.setitem(sys.modules, "agent.rag", fake)
    # `from agent import rag` looks at the package attribute before sys.modules, and
    # the real module is already there when another test file imported it first.
    monkeypatch.setattr(agent, "rag", fake, raising=False)
    return fake


# --- 3a: the definition ---


def test_definition_has_the_name_claude_will_call():
    assert TOOL_SEARCH_DOCS["name"] == "search_docs"


def test_description_tells_claude_when_to_use_it():
    description = TOOL_SEARCH_DOCS["description"]
    assert isinstance(description, str)
    assert len(description) >= 150, "Say what it returns, when to use it and when not"
    assert "find_order" in description and "find_policy" in description, (
        "Say which questions belong to the other two tools"
    )


def test_schema_takes_only_a_required_query_string():
    schema = TOOL_SEARCH_DOCS["input_schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"query"}, (
        "Only the query: k and min_score are decided by the code"
    )
    assert schema["properties"]["query"]["type"] == "string"
    assert len(schema["properties"]["query"].get("description", "")) >= 20, (
        "Tell Claude what a good query looks like"
    )
    assert schema["required"] == ["query"]


# --- 3b: the executor ---


def test_it_searches_with_claudes_query_the_cached_index_and_the_faq(fake_rag):
    fake_rag.results = ["Open 9 to 6."]
    run_tool("search_docs", {"query": "Are you open on Saturdays?"}, MAIL)
    assert fake_rag.calls == [
        {
            "query": "Are you open on Saturdays?",
            "index": "THE INDEX",
            "docs": fake_rag.FAQ_DOCS,
        }
    ], "One search, with the query, rag.faq_index() and rag.FAQ_DOCS, and nothing else"


def test_one_document_comes_back_as_its_text(fake_rag):
    fake_rag.results = ["Open 9 to 6."]
    assert run_tool("search_docs", {"query": "hours?"}, MAIL) == "Open 9 to 6."


def test_several_documents_are_joined_by_a_blank_line_in_order(fake_rag):
    fake_rag.results = ["Gift cards last 12 months.", "Open 9 to 6."]
    assert run_tool("search_docs", {"query": "gift cards"}, MAIL) == (
        "Gift cards last 12 months.\n\nOpen 9 to 6."
    )


def test_no_documents_gives_a_clear_sentence_not_an_empty_answer(fake_rag):
    fake_rag.results = []
    assert (
        run_tool("search_docs", {"query": "capital of France"}, MAIL) == NOTHING_FOUND
    )


def test_claude_cannot_change_k_or_the_minimum_score(fake_rag):
    fake_rag.results = ["Open 9 to 6."]
    run_tool("search_docs", {"query": "hours?", "k": 50, "min_score": -1}, MAIL)
    call = fake_rag.calls[0]
    assert call.get("k") != 50 and call.get("min_score") != -1
    assert 50 not in call.values() and -1 not in call.values()


def test_the_query_is_what_claude_asked_not_the_email_body(fake_rag):
    mail = Email(message_id="t", sender="a@b.com", subject="s", body="A long email")
    run_tool("search_docs", {"query": "invoice with tax ID"}, mail)
    assert fake_rag.calls[0]["query"] == "invoice with tax ID"


# --- 3c: where it's registered ---


def test_it_is_registered_as_an_executor():
    assert tools.EXECUTORS["search_docs"] is tools._run_search_docs


def test_only_the_store_profile_has_it():
    assert TOOL_SEARCH_DOCS in PROFILES["store"].tools
    assert TOOL_SEARCH_DOCS not in PROFILES["freelance"].tools
    assert [tool["name"] for tool in PROFILES["store"].tools] == [
        "find_order",
        "find_policy",
        "search_docs",
    ]


# --- Gaps closed after a mutation audit of these tests ---


def test_query_is_free_text_not_a_list_of_options():
    assert "enum" not in TOOL_SEARCH_DOCS["input_schema"]["properties"]["query"], (
        "The query is free text: Claude writes the question, it doesn't pick a topic"
    )


def test_three_documents_keep_the_order_search_docs_gave(fake_rag):
    fake_rag.results = ["Open 9 to 6.", "Gift cards last 12 months.", "A third doc."]
    assert run_tool("search_docs", {"query": "x"}, MAIL) == (
        "Open 9 to 6.\n\nGift cards last 12 months.\n\nA third doc."
    ), "All the documents, in the order search_docs returned them (best first)"


def test_a_failing_search_is_not_reported_as_no_documents(fake_rag, monkeypatch):
    def broken(query, index, docs, **kwargs):
        raise RuntimeError("the model is not installed")

    monkeypatch.setattr(fake_rag, "search_docs", broken)
    try:
        answer = run_tool("search_docs", {"query": "hours?"}, MAIL)
    except RuntimeError:
        return  # letting the error out is fine: drafter.py decides what to do
    assert answer != NOTHING_FOUND, (
        "Don't hide errors: 'no document' would make Claude tell the customer "
        "the store has no answer, when the search never ran"
    )


@pytest.mark.parametrize("tool_input", [{"query": ""}, {}])
def test_it_never_searches_with_the_email_body(fake_rag, tool_input):
    mail = Email(message_id="t", sender="a@b.com", subject="s", body="A long email")
    try:
        run_tool("search_docs", tool_input, mail)
    except KeyError:
        pass  # same as find_order without order_id
    assert all("A long email" not in str(call["query"]) for call in fake_rag.calls), (
        "The email body is attacker-controlled text: only Claude's query is searched"
    )


def test_importing_tools_and_profiles_does_not_import_rag_in_a_clean_process():
    code = (
        "import sys, agent.tools, agent.profiles;"
        "bad = [m for m in ('agent.rag', 'sentence_transformers', 'torch') if m in sys.modules];"
        "assert not bad, bad"
    )
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        "Import rag inside _run_search_docs, not at the top of tools.py\n"
        + result.stderr[-300:]
    )


def test_description_length_does_not_count_indentation():
    assert len(" ".join(TOOL_SEARCH_DOCS["description"].split())) >= 150


def test_description_warns_the_documents_may_not_answer():
    description = TOOL_SEARCH_DOCS["description"].lower()
    assert any(
        clue in description
        for clue in ["may not", "might not", "not always", "not necessarily", "closest"]
    ), "Say the documents are the closest ones and may not answer the question"


def test_the_index_comes_from_faq_index_on_every_call(fake_rag):
    run_tool("search_docs", {"query": "a"}, MAIL)
    fake_rag.faq_index = lambda: "ANOTHER INDEX"
    run_tool("search_docs", {"query": "b"}, MAIL)
    assert [call["index"] for call in fake_rag.calls] == ["THE INDEX", "ANOTHER INDEX"]


def test_the_same_query_is_searched_again(fake_rag):
    fake_rag.results = ["Open 9 to 6."]
    run_tool("search_docs", {"query": "same question"}, MAIL)
    fake_rag.results = []
    assert run_tool("search_docs", {"query": "same question"}, MAIL) == NOTHING_FOUND
    assert len(fake_rag.calls) == 2


def test_documents_are_not_rewritten(fake_rag):
    fake_rag.results = ["First line.\nSecond line.", "  Indented doc.  "]
    assert run_tool("search_docs", {"query": "x"}, MAIL) == (
        "First line.\nSecond line.\n\n  Indented doc.  "
    )


def test_the_query_is_not_trimmed_or_cut(fake_rag):
    query = "  ¿Necesito factura A con CUIT? " + "detalle " * 30
    run_tool("search_docs", {"query": query}, MAIL)
    assert fake_rag.calls[0]["query"] == query
