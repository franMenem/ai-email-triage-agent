"""
Agente de mails — Paso 4: qué pasa cuando la API de Claude falla
================================================================

Leé antes:
- La sección "Handling errors" del README del SDK:
  https://github.com/anthropics/anthropic-sdk-python#handling-errors
- La sección "Retries" (justo abajo): el SDK YA reintenta solo.

Contexto: tu agente es CLIENTE de la API de Anthropic. Hoy, si esa API
falla, `draft_reply` tira una excepción, FastAPI responde un 500 genérico
y... hay un bug escondido peor (lo encuentra el test 1).

Consigna — modificá `receive` en main.py para que:

1) Errores TEMPORALES (vale la pena reintentar más tarde) → responder 503:
   - anthropic.APIConnectionError  (sin red, timeout)
   - anthropic.RateLimitError      (429, demasiados pedidos)
   - anthropic.InternalServerError (5xx, Anthropic tiene un problema)

2) Errores PERMANENTES (reintentar no sirve, hay que arreglar algo) → 500:
   - cualquier otro anthropic.APIStatusError (ej: 401 API key inválida,
     400 pedido mal armado)

3) Si falló la llamada a Claude, el mail NO cuenta como procesado: si el
   mismo message_id llega de nuevo, se tiene que procesar (no "duplicate").

Para responder un código de error en FastAPI:
    from fastapi import HTTPException
    raise HTTPException(status_code=503, detail="...")

Pistas:
- El orden de los `except` importa: Python entra en el PRIMERO que coincide.
  RateLimitError e InternalServerError son "hijas" de APIStatusError.
  ¿Cuál tiene que ir antes?
- Para el punto 3, mirá en qué momento hacés `base_de_datos.add(...)`.

Pregunta (contestala en un comentario en main.py, sin código):
    El SDK reintenta solo 2 veces por defecto (max_retries=2). Cuando a tu
    `except` le llega un RateLimitError, ¿cuántos intentos se hicieron en total?

Correlo con:
    uv run python test_errores.py
"""

import anthropic
import httpx
from fastapi.testclient import TestClient

import main
from models import Borrador

client = TestClient(main.app)

# Pedido "de mentira" que necesitan las excepciones del SDK para construirse
REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def fake_que_falla(error):
    """Devuelve un draft_reply falso que siempre tira `error`."""

    async def fake(email):
        raise error

    return fake


async def fake_ok(email):
    return Borrador(borrador="BORRADOR OK", notas_internas="NOTAS OK")


def mail(message_id):
    return {
        "message_id": message_id,
        "sender": "cliente@ejemplo.com",
        "subject": "Consulta",
        "body": "Hola, ¿cuándo llega mi pedido?",
    }


def test_sin_conexion_da_503_y_se_puede_reintentar():
    main.draft_reply = fake_que_falla(anthropic.APIConnectionError(request=REQ))
    respuesta = client.post("/emails", json=mail("err-1"))
    assert respuesta.status_code == 503, f"Esperaba 503, vino {respuesta.status_code}"

    # Anthropic volvió: el mismo mail tiene que procesarse, no ser "duplicate"
    main.draft_reply = fake_ok
    reintento = client.post("/emails", json=mail("err-1"))
    assert reintento.status_code == 200
    assert reintento.json()["status"] == "received", (
        "El mail que falló quedó marcado como visto: se perdió para siempre"
    )


def test_rate_limit_da_503():
    error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=REQ), body=None
    )
    main.draft_reply = fake_que_falla(error)
    respuesta = client.post("/emails", json=mail("err-2"))
    assert respuesta.status_code == 503


def test_error_del_servidor_de_anthropic_da_503():
    error = anthropic.InternalServerError(
        "boom", response=httpx.Response(500, request=REQ), body=None
    )
    main.draft_reply = fake_que_falla(error)
    respuesta = client.post("/emails", json=mail("err-3"))
    assert respuesta.status_code == 503


def test_api_key_invalida_da_500_y_se_puede_reintentar():
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=REQ), body=None
    )
    main.draft_reply = fake_que_falla(error)
    respuesta = client.post("/emails", json=mail("err-4"))
    assert respuesta.status_code == 500

    main.draft_reply = fake_ok
    reintento = client.post("/emails", json=mail("err-4"))
    assert reintento.json()["status"] == "received"


if __name__ == "__main__":
    test_sin_conexion_da_503_y_se_puede_reintentar()
    test_rate_limit_da_503()
    test_error_del_servidor_de_anthropic_da_503()
    test_api_key_invalida_da_500_y_se_puede_reintentar()
    print("¡Paso 4 del agente completo! 🎉")
