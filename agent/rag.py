"""
FAQ search (RAG): finds the store documents that answer a customer's question.

The store already answers from tables (find_order, find_policy in tools.py).
This module is for questions that are not in a table: opening hours, invoices,
gift cards... It compares meaning, not words: each FAQ document is turned into
a vector once (the index), each query is turned into a vector, and the closest
documents win.

    index = faq_index()
    search_docs("Are you open on Saturdays?", index, FAQ_DOCS)
    # -> ["Customer support is available Monday to Friday, ..."]

The embedding model runs locally (sentence-transformers) and is loaded the
first time something is embedded, not when this module is imported. It needs
the optional "rag" dependency group:
    uv run --group rag ...

How well it finds the right document is measured in evals/run_retrieval_evals.py.
"""

import json
import math
from pathlib import Path

# FAQ knowledge base: {doc_id: text}. Topics that are not in the policies table.
FAQ_DOCS = json.loads(
    (Path(__file__).parents[1] / "data" / "faq.json").read_text(encoding="utf-8")
)

# Multilingual (the customers write in Spanish and English). 768-number vectors.
MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"

# Chosen by looking at real scores, not by feel: see evals/results/retrieval.json.
# They only hold for this model; another model needs them measured again.
MIN_SCORE = 0.40  # below this, a document is not worth showing to Claude
TOP_K = 3  # at most this many documents per query

_model = None
_faq_index = None


def _get_model():
    """The embedding model, loaded on first use (it's about 1 GB)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text):
    """The text as a vector: a plain list of floats."""
    return _get_model().encode(text).tolist()


def cosine_similarity(a, b):
    """How close two vectors point, from -1 to 1. A zero vector gives 0.0."""
    norm_a = math.sqrt(sum([x * x for x in a]))
    norm_b = math.sqrt(sum([x * x for x in b]))
    if norm_b * norm_a == 0:
        return 0.0
    p_product = 0.0
    for i, j in zip(a, b):
        p_product += i * j

    return p_product / (norm_a * norm_b)


def build_index(docs):
    """{doc_id: text} -> {doc_id: vector}. The slow part: done once, then reused."""
    new = {}
    for k, v in docs.items():
        new[k] = embed(v)
    return new


def score_docs(query, index):
    """Every document with its similarity to the query: [(doc_id, score), ...],
    best first; ties go by doc_id. Nothing is cut or filtered here."""
    new = {}
    vector = embed(query)
    for doc_id, v in index.items():
        new[doc_id] = cosine_similarity(vector, v)
    new = sorted(new.items(), key=lambda par: (-par[1], par[0]))
    return new


def search_docs(query, index, docs, k=TOP_K, min_score=MIN_SCORE):
    """The texts of the documents closest to the query, best first: only those
    scoring at least min_score, and at most k. [] if none gets there."""
    response = []
    for score in score_docs(query, index):
        if score[1] >= min_score:
            response.append(docs[score[0]])
    return response[:k]


def faq_index():
    """The index of FAQ_DOCS, built the first time it's needed and reused after."""
    global _faq_index
    if _faq_index is None:
        _faq_index = build_index(FAQ_DOCS)
    return _faq_index
