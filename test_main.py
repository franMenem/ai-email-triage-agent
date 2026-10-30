"""
Tests del agente (pasos 1 y 2). Correlos con:
    uv run python test_main.py

NO llaman a la API real de Claude: reemplazamos `draft_reply` por una
función falsa que devuelve siempre el mismo texto y anota a quién la llamaron.
Así los tests son gratis, rápidos y no dependen de internet.
"""

from fastapi.testclient import TestClient

import main
from models import Borrador

llamadas = []


async def fake_draft_reply(email):
    llamadas.append(email.message_id)
    return Borrador(borrador="BORRADOR DE PRUEBA", notas_internas="NOTAS DE PRUEBA")


main.draft_reply = fake_draft_reply  # el endpoint va a usar la falsa
client = TestClient(main.app)


def test_recibe_mail_valido():
    mail = {
        "message_id": "abc-1",
        "sender": "cliente@ejemplo.com",
        "subject": "Consulta por mi pedido",
        "body": "Hola, ¿cuándo llega mi pedido?",
    }
    respuesta = client.post("/emails", json=mail)
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "status": "received",
        "message_id": "abc-1",
        "draft": "BORRADOR DE PRUEBA",
        "notas": "NOTAS DE PRUEBA",
    }


def test_falta_un_campo_da_422_y_no_llama_al_llm():
    mail_incompleto = {"message_id": "abc-2", "sender": "cliente@ejemplo.com"}
    respuesta = client.post("/emails", json=mail_incompleto)
    assert respuesta.status_code == 422
    assert "abc-2" not in llamadas


def test_duplicado_no_vuelve_a_llamar_al_llm():
    mail = {
        "message_id": "abc-3",
        "sender": "cliente@ejemplo.com",
        "subject": "Reclamo",
        "body": "El producto llegó roto.",
    }
    primera = client.post("/emails", json=mail)
    segunda = client.post("/emails", json=mail)
    assert primera.json()["draft"] == "BORRADOR DE PRUEBA"
    assert segunda.json() == {"status": "duplicate", "message_id": "abc-3"}
    assert llamadas.count("abc-3") == 1, "El duplicado no tiene que gastar otra llamada"


if __name__ == "__main__":
    test_recibe_mail_valido()
    test_falta_un_campo_da_422_y_no_llama_al_llm()
    test_duplicado_no_vuelve_a_llamar_al_llm()
    print("¡Paso 2 del agente completo! 🎉")
