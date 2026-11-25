"""Experiment (not used by the agent): does adding a TF-IDF term-overlap bonus to the
embedding similarity find more documents? Result: better on dev, worse on test, so it
was dropped. See "Tried and dropped" in the README.

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
    print(
        f"\n=== stem={stem}   (dev: found/22 rejected/10 | test: found/16 rejected/8)"
    )
    rows = []
    for w in (0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4):
        for m in (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
            f, na, r, nn = measure("dev", tp, w, m)
            rows.append((f / na + r / nn, f, r, w, m))
    rows.sort(reverse=True)
    for bal, f, r, w, m in rows[:8]:
        tf, tna, tr, tnn = measure("test", tp, w, m)
        print(
            f"w={w:<5} min={m:<5} dev {f}/22 {r}/10  bal={bal:.3f} | test {tf}/{tna} {tr}/{tnn}"
        )
    for w, m in ((0, 0.4), (0, 0.45)):
        f, na, r, nn = measure("dev", tp, w, m)
        tf, tna, tr, tnn = measure("test", tp, w, m)
        print(f"BASE w={w} min={m}: dev {f}/{na} {r}/{nn} | test {tf}/{tna} {tr}/{tnn}")
