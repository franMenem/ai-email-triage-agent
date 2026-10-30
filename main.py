"""
Agente de mails — Paso 1: el endpoint donde llegan los mails
============================================================

Por ahora NO hay Gmail ni LLM: vos le vas a mandar mails "falsos" en JSON
y la API solo tiene que recibirlos, validarlos y confirmar.

1) Modelo `Email` (Pydantic) con 4 campos, todos texto y obligatorios:
   - message_id  -> ID único del mail (después lo usamos para idempotencia)
   - sender      -> quién lo manda
   - subject     -> asunto
   - body        -> cuerpo

2) Endpoint `POST /emails` que recibe un `Email` y responde:
   {"status": "received", "message_id": <el message_id del mail>}

3) Idempotencia: si llega OTRA VEZ un mail con un message_id que ya
   recibiste, NO lo proceses de nuevo; respondé:
   {"status": "duplicate", "message_id": <el message_id>}
   (Por ahora alcanza con guardar los IDs en memoria, en una estructura
   de Python. ¿Cuál es la ideal para "¿ya vi este ID?"?)

Si falta un campo, FastAPI ya responde 422 solo, gracias a Pydantic:
no tenés que programar esa validación.

Para levantar el servidor y probarlo a mano:
    fastapi dev main.py
y abrí http://127.0.0.1:8000/docs
"""

import anthropic
from fastapi import FastAPI, HTTPException

from drafter import draft_reply
from models import Email

app = FastAPI()
# usamos SET pq no queremos que se dupliquen los mails que recibimos.
base_de_datos = set()


@app.post("/emails")
async def receive(email: Email):
    try:
        if email.message_id not in base_de_datos:
            draft = await draft_reply(email)
            base_de_datos.add(email.message_id)
            return {
                "status": "received",
                "message_id": email.message_id,
                "draft": draft.borrador,
                "notas": draft.notas_internas,
            }
        else:
            return {
                "status": "duplicate",
                "message_id": email.message_id,
            }
    except anthropic.APIConnectionError:
        raise HTTPException(
            status_code=503, detail="El servidor no se encuentra disponible"
        )
    except anthropic.APIStatusError as e:
        if e.status_code == 429 or e.status_code >= 500:
            raise HTTPException(status_code=503)
        else:
            raise HTTPException(
                status_code=500, detail="El servicio no esta disponible en este momento"
            )
