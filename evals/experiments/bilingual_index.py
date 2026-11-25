"""Experiment (not used by the agent): does embedding each FAQ document in English and
in Spanish, and keeping the higher score, find more documents? Result: identical at the
current minimum score (0.40) and a query or two better at higher ones. Not a measurable
gain, so it isn't used. See "Tried and dropped" in the README.

Run it with:
    uv run --group rag python -m evals.experiments.bilingual_index
"""

import json
from pathlib import Path

from agent import rag

ES = {
    "opening_hours": "Atención al cliente está disponible de lunes a viernes, de 9 a 18 horas. Cerramos los fines de semana y los feriados.",
    "order_tracking": "Podés seguir tu pedido en nuestra web con el número de pedido. La información de seguimiento se actualiza cada 24 horas.",
    "invoices": "La factura se envía por mail automáticamente cuando se despacha el pedido. Si necesitás una factura con el CUIT de tu empresa, pedila dentro de los 30 días de la compra.",
    "gift_cards": "Las gift cards (tarjetas de regalo) se compran online, de 10 a 500 dólares. Valen por 12 meses y no se pueden canjear por dinero.",
    "stock": "Si un producto está agotado, podés anotarte en su página para recibir un mail cuando vuelva a estar disponible. La reposición suele tardar de 2 a 3 semanas.",
    "wholesale": "Ofrecemos precios mayoristas para compras de 20 unidades o más. Escribinos con los datos de tu empresa para recibir un presupuesto.",
    "physical_stores": "Somos una tienda solo online: no tenemos locales físicos ni showroom. Todas las compras se hacen por la web.",
    "account": "Podés restablecer tu contraseña desde la página de ingreso con el enlace 'Olvidé mi contraseña'. Para cambiar el mail de tu cuenta, escribí a atención al cliente.",
}
assert set(ES) == set(rag.FAQ_DOCS)
cases = json.loads((Path(__file__).parents[1] / "retrieval_cases.json").read_text())
ix_en = rag.build_index(rag.FAQ_DOCS)
ix_es = rag.build_index(ES)
for c in cases:
    v = rag.embed(c["query"])
    c["en"] = {d: rag.cosine_similarity(v, x) for d, x in ix_en.items()}
    c["es"] = {d: rag.cosine_similarity(v, x) for d, x in ix_es.items()}
    c["max"] = {d: max(c["en"][d], c["es"][d]) for d in ix_en}


def measure(split, key, m, k=3, detail=False):
    f = r = na = nn = 0
    fails = []
    for c in cases:
        if c["split"] != split:
            continue
        sc = sorted(c[key].items(), key=lambda p: (-p[1], p[0]))
        ids = [d for d, s in sc if s >= m][:k]
        if c["expected"] is None:
            nn += 1
            ok = ids == []
            r += ok
        else:
            na += 1
            ok = c["expected"] in ids
            f += ok
        if not ok:
            fails.append(
                (c["query"], c["expected"], [(d, round(s, 2)) for d, s in sc[:2]])
            )
    return (f, na, r, nn, fails) if detail else (f, na, r, nn)


for key in ("en", "max"):
    print(f"\n=== {key}")
    for m in (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6):
        d = measure("dev", key, m)
        t = measure("test", key, m)
        print(
            f"min={m:<5} dev {d[0]}/{d[1]} {d[2]}/{d[3]} | test {t[0]}/{t[1]} {t[2]}/{t[3]}"
        )
for key, m in (("en", 0.4), ("max", 0.45), ("max", 0.5)):
    for split in ("dev", "test"):
        print(f"\nfallas {key} min={m} {split}:")
        for x in measure(split, key, m, detail=True)[4]:
            print("  ", x)
