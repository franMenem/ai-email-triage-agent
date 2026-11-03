"""
Agente de mails — Paso 5: clasificar cada mail por intención y urgencia
======================================================================

Además del borrador, Claude tiene que devolver dos campos nuevos:
- intencion: "estado_pedido", "devolucion", "reclamo", "consulta_general" u "otro"
- urgencia:  "alta", "media" o "baja"

(Qué significa cada valor ya está explicado en prompts.py.)

1) models.py: sumá los dos campos a `Borrador`. Tienen que aceptar SOLO esos
   valores; cualquier otro texto tiene que dar error de validación.
   Pista: mirá `Literal` del módulo `typing`.

2) drafter.py: el JSON schema hoy está escrito adentro de `llamar_a_claude`.
   Sacalo a una constante a nivel módulo llamada `SCHEMA_BORRADOR` y usala ahí.
   Después sumale los dos campos nuevos, cada uno con "enum" y su lista de
   valores, y agregalos a "required".

3) main.py: la respuesta de un mail nuevo suma "intencion" y "urgencia".

4) test_main.py: el `Borrador` falso ahora necesita los campos nuevos
   (y el dict esperado en test_recibe_mail_valido, también).

Correlo con: uv run python test_clasificacion.py   (no usa la API: es gratis)
"""

from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from drafter import SCHEMA_BORRADOR
from models import Borrador

INTENCIONES = ["estado_pedido", "devolucion", "reclamo", "consulta_general", "otro"]
URGENCIAS = ["alta", "media", "baja"]


def test_borrador_acepta_valores_validos():
    b = Borrador(borrador="x", notas_internas="y", intencion="reclamo", urgencia="alta")
    assert b.intencion == "reclamo"
    assert b.urgencia == "alta"


def test_borrador_rechaza_valores_inventados():
    try:
        Borrador(borrador="x", notas_internas="y", intencion="queja", urgencia="alta")
        assert False, "intencion='queja' no existe: tendría que dar ValidationError"
    except ValidationError:
        pass
    try:
        Borrador(borrador="x", notas_internas="y", intencion="otro", urgencia="urgente")
        assert False, "urgencia='urgente' no existe: tendría que dar ValidationError"
    except ValidationError:
        pass


def test_schema_tiene_los_campos_nuevos():
    props = SCHEMA_BORRADOR["properties"]
    assert sorted(props["intencion"]["enum"]) == sorted(INTENCIONES)
    assert sorted(props["urgencia"]["enum"]) == sorted(URGENCIAS)
    for campo in ["borrador", "notas_internas", "intencion", "urgencia"]:
        assert campo in SCHEMA_BORRADOR["required"], f"Falta '{campo}' en required"
    assert SCHEMA_BORRADOR["additionalProperties"] is False


def test_endpoint_devuelve_la_clasificacion():
    async def fake_draft_reply(email):
        return Borrador(
            borrador="B", notas_internas="N", intencion="devolucion", urgencia="media"
        )

    main.draft_reply = fake_draft_reply
    mail = {
        "message_id": "clasif-1",
        "sender": "cliente@ejemplo.com",
        "subject": "Quiero devolver",
        "body": "Me quedó chico, ¿cómo lo devuelvo?",
    }
    datos = TestClient(main.app).post("/emails", json=mail).json()
    assert datos["intencion"] == "devolucion"
    assert datos["urgencia"] == "media"


if __name__ == "__main__":
    test_borrador_acepta_valores_validos()
    test_borrador_rechaza_valores_inventados()
    test_schema_tiene_los_campos_nuevos()
    test_endpoint_devuelve_la_clasificacion()
    print("¡Paso 5 del agente completo! 🎉")
