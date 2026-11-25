"""Experiment (not used by the agent): does adding a TF-IDF term-overlap bonus to the
embedding similarity find more documents? Result: at the same minimum score, a small bonus
adds one dev query and nothing on test, and larger bonuses let wrong documents through.
Not a measurable gain, so it isn't used. It is a weighted term overlap, not BM25. See "Tried and dropped" in the README.

Run it with:
    uv run --group rag python -m evals.experiments.hybrid_search
"""

import json
import math
import string
from pathlib import Path

from agent import rag

STOP = set(
    """el la los las de del un una y a en es con que mi tu me se para por cada si no al lo le entre todo cómo qué cuándo dónde
the a an and or of to in on for with is are was be do does did can could will would you your i my me we our us it its this that
what when where how which who there have has had from at by as if not no so please hi hello hola puedo quiero necesito tienen hacen son
su sus nos les te o e u ya muy más mas hay esta este esto""".split()
)


def tokenize(text, stem):
    out = []
    for w in text.lower().split():
        w = w.strip(string.punctuation + "¿¡")
        if not w or w in STOP:
            continue
        if stem and len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        out.append(w)
    return out


def make(stem):
    toks = {d: set(tokenize(t, stem)) for d, t in rag.FAQ_DOCS.items()}
    df = {}
    for s in toks.values():
        for t in s:
            df[t] = df.get(t, 0) + 1
    n = len(toks)
    idf = lambda t: math.log(n / df[t]) + 1 if t in df else 0.0

    def term_part(query, doc_id):
        q = set(tokenize(query, stem))
        known = sum(idf(t) for t in q)
        if not known:
            return 0.0
        return sum(idf(t) for t in q if t in toks[doc_id]) / known

    return term_part


cases = json.loads((Path(__file__).parents[1] / "retrieval_cases.json").read_text())
index = rag.faq_index()
for c in cases:
    c["cos"] = dict(rag.score_docs(c["query"], index))


def measure(split, term_part, w, min_score, k=3):
    found = rej = na = nn = 0
    for c in cases:
        if c["split"] != split:
            continue
        sc = sorted(
            ((d, s + w * term_part(c["query"], d)) for d, s in c["cos"].items()),
            key=lambda p: (-p[1], p[0]),
        )
        ids = [d for d, s in sc if s >= min_score][:k]
        if c["expected"] is None:
            nn += 1
            rej += ids == []
        else:
            na += 1
            found += c["expected"] in ids
    return found, na, rej, nn


for stem in (False, True):
    tp = make(stem)
    print(f"\n=== plural stripping={stem}   (found, rejected)")
    print(
        "Compare each row with weight 0 AT THE SAME minimum score: that is the baseline."
    )
    for m in (0.4, 0.45):
        for w in (0, 0.05, 0.1, 0.2, 0.3):
            f, na, r, nn = measure("dev", tp, w, m)
            tf, tna, tr, tnn = measure("test", tp, w, m)
            print(
                f"min={m:<5} weight={w:<5} dev {f}/{na} {r}/{nn} | test {tf}/{tna} {tr}/{tnn}"
            )
