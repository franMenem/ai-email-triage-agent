"""FAQ search: similarity, ranking, and the minimum score and maximum count.

Most tests need no embedding model: they use small hand-made vectors and a fake
`embed`. The few that check real meaning are skipped when sentence-transformers
isn't installed (it's the optional "rag" group):
    uv run --group rag pytest tests/test_rag.py
"""

import math

import pytest

import rag

# Hand-made vectors: the query points along [1, 0]; each doc's score against it
# is easy to work out by eye.
QUERY = "the question"
INDEX = {
    "same": [1.0, 0.0],  # score 1
    "close": [1.0, 1.0],  # score 0.7071...
    "unrelated": [0.0, 1.0],  # score 0
    "opposite": [-1.0, 0.0],  # score -1
}
DOCS = {"same": "S", "close": "C", "unrelated": "U", "opposite": "O"}
CLOSE = 1 / math.sqrt(2)


@pytest.fixture
def fake_embed(monkeypatch):
    """Replaces rag.embed: the query becomes [1, 0], and every call is recorded."""
    calls = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0] if text == QUERY else [float(len(text)), 1.0]

    monkeypatch.setattr(rag, "embed", embed)
    return calls


# --- cosine_similarity ---


def test_cosine_depends_on_direction_not_length():
    assert math.isclose(rag.cosine_similarity([1, 2, 3], [2, 4, 6]), 1.0)
    assert math.isclose(rag.cosine_similarity([1, 0], [-1, 0]), -1.0)
    assert math.isclose(rag.cosine_similarity([1, 0], [0, 1]), 0.0, abs_tol=1e-12)
    assert math.isclose(rag.cosine_similarity([1, 0], [1, 1]), CLOSE)
    assert math.isclose(rag.cosine_similarity([1, -1], [1, -1]), 1.0), (
        "[1, -1] is not the zero vector"
    )


def test_cosine_of_a_zero_vector_is_zero_on_either_side():
    assert rag.cosine_similarity([0, 0], [1, 1]) == 0.0
    assert rag.cosine_similarity([1, 1], [0, 0]) == 0.0
    assert isinstance(rag.cosine_similarity([0, 0], [1, 1]), float), "0.0, not 0"


# --- build_index ---


def test_build_index_embeds_the_texts_it_receives(fake_embed):
    docs = {"x": "hello", "y": "hi"}
    assert rag.build_index(docs) == {"x": [5.0, 1.0], "y": [2.0, 1.0]}
    assert fake_embed == ["hello", "hi"], "The texts, not the ids, once each"
    assert docs == {"x": "hello", "y": "hi"}, "The input is not modified"
    assert rag.build_index({}) == {}


# --- score_docs ---


def test_score_docs_returns_every_doc_best_first(fake_embed):
    scored = rag.score_docs(QUERY, INDEX)
    assert isinstance(scored, list)
    assert [doc_id for doc_id, _ in scored] == [
        "same",
        "close",
        "unrelated",
        "opposite",
    ]
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in scored)
    scores = [score for _, score in scored]
    assert all(isinstance(score, float) for score in scores)
    assert math.isclose(scores[0], 1.0)
    assert math.isclose(scores[1], CLOSE), "The cosine similarity, not rounded"
    assert math.isclose(scores[2], 0.0, abs_tol=1e-12)
    assert math.isclose(scores[3], -1.0), "Negative scores stay, and stay negative"


def test_score_docs_breaks_ties_by_doc_id(fake_embed):
    tied = {"b": [2.0, 0.0], "c": [0.0, 1.0], "a": [1.0, 0.0], "d": [3.0, 0.0]}
    assert [doc_id for doc_id, _ in rag.score_docs(QUERY, tied)] == ["a", "b", "d", "c"]


def test_score_docs_embeds_the_query_once_and_nothing_else(fake_embed):
    rag.score_docs(QUERY, INDEX)
    assert fake_embed == [QUERY]


def test_score_docs_with_an_empty_index(fake_embed):
    assert rag.score_docs(QUERY, {}) == []


# --- search_docs ---


def test_search_docs_returns_texts_best_first(fake_embed):
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=-1) == [
        "S",
        "C",
        "U",
        "O",
    ], "Texts from `docs`, every one that reaches min_score, best first"


def test_search_docs_returns_at_most_k(fake_embed):
    assert rag.search_docs(QUERY, INDEX, DOCS, k=1, min_score=-1) == ["S"]
    assert rag.search_docs(QUERY, INDEX, DOCS, k=3, min_score=-1) == ["S", "C", "U"]
    assert rag.search_docs(QUERY, INDEX, DOCS, k=0, min_score=-1) == []


def test_search_docs_drops_what_is_below_min_score(fake_embed):
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=0.5) == ["S", "C"]
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=0) == ["S", "C", "U"], (
        "0 is a real threshold: it keeps a score of 0 and drops the negative one"
    )
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=2) == []


def test_search_docs_min_score_is_inclusive_and_not_rounded(fake_embed):
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=CLOSE) == ["S", "C"]
    assert rag.search_docs(QUERY, INDEX, DOCS, k=10, min_score=CLOSE + 1e-6) == ["S"]


def test_search_docs_applies_both_limits(fake_embed):
    assert rag.search_docs(QUERY, INDEX, DOCS, k=1, min_score=0.5) == ["S"]


def test_search_docs_defaults_are_the_measured_constants(fake_embed):
    many_index = {doc_id: [1.0, 0.0] for doc_id in "abcdefgh"}
    many_docs = {doc_id: doc_id.upper() for doc_id in "abcdefgh"}
    assert (
        rag.search_docs(QUERY, many_index, many_docs) == list("ABCDEFGH")[: rag.TOP_K]
    )

    edge = rag.MIN_SCORE  # a vector whose score against [1, 0] is exactly `edge`
    index = {
        "in": [edge + 0.01, math.sqrt(1 - (edge + 0.01) ** 2)],
        "out": [edge - 0.01, math.sqrt(1 - (edge - 0.01) ** 2)],
    }
    assert rag.search_docs(QUERY, index, {"in": "IN", "out": "OUT"}) == ["IN"]


def test_search_docs_takes_the_texts_from_docs_not_from_the_faq(fake_embed):
    doc_id = next(iter(rag.FAQ_DOCS))
    assert rag.search_docs(QUERY, {doc_id: [1.0, 0.0]}, {doc_id: "OTHER TEXT"}) == [
        "OTHER TEXT"
    ], "Even when the id also exists in FAQ_DOCS"


def test_search_docs_with_nothing_to_search(fake_embed):
    assert rag.search_docs(QUERY, {}, {}) == []


def test_search_docs_does_not_embed_the_documents_again(fake_embed):
    rag.search_docs(QUERY, INDEX, DOCS)
    assert fake_embed == [QUERY]


# --- the FAQ and its index ---


def test_the_faq_is_a_dict_of_non_empty_texts():
    assert len(rag.FAQ_DOCS) >= 5
    assert all(
        isinstance(doc_id, str) and isinstance(text, str) and text.strip()
        for doc_id, text in rag.FAQ_DOCS.items()
    )


def test_the_faq_index_is_built_once(fake_embed, monkeypatch):
    monkeypatch.setattr(rag, "_faq_index", None)
    first = rag.faq_index()
    assert set(first) == set(rag.FAQ_DOCS)
    assert rag.faq_index() is first
    assert sorted(fake_embed) == sorted(rag.FAQ_DOCS.values()), (
        "Each FAQ text embedded once, even after asking for the index twice"
    )


# --- with the real model (skipped without the "rag" dependency group) ---


@pytest.fixture(scope="module")
def real_index():
    pytest.importorskip("sentence_transformers")
    return rag.build_index(rag.FAQ_DOCS)


def test_embed_returns_the_models_vector_as_plain_floats(real_index):
    vector = rag.embed("Do gift cards expire?")
    assert isinstance(vector, list) and len(vector) == 768
    assert all(isinstance(x, float) for x in vector)
    expected = rag._get_model().encode("Do gift cards expire?")
    assert all(
        math.isclose(x, float(y), abs_tol=1e-6) for x, y in zip(vector, expected)
    )


@pytest.mark.parametrize(
    "query, doc_id",
    [
        ("Do gift cards expire?", "gift_cards"),
        ("I forgot my password", "account"),
        ("¿Atienden los sábados?", "opening_hours"),
    ],
)
def test_the_closest_document_is_the_one_that_answers(real_index, query, doc_id):
    assert rag.score_docs(query, real_index)[0][0] == doc_id


def test_a_question_outside_the_faq_gets_no_documents(real_index):
    assert (
        rag.search_docs("¿Cuál es la capital de Francia?", real_index, rag.FAQ_DOCS)
        == []
    )
