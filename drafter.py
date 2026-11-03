"""
Agente de mails — Paso 2: Claude redacta el borrador
====================================================

Implementá `draft_reply(email)`: recibe un `Email` y devuelve (str) un
borrador de respuesta escrito por Claude. Rubro: atención al cliente.

Requisitos:
1) Usá el cliente ASYNC del SDK oficial (`anthropic.AsyncAnthropic`), creado
   UNA sola vez a nivel módulo (no en cada llamada). La API key la toma solo
   de la variable de entorno ANTHROPIC_API_KEY: no la escribas en el código.
2) Modelo: "claude-opus-5-5".
3) System prompt (lo escribís VOS, es la parte más importante): define el rol,
   el tono, el idioma de la respuesta, qué NO hacer (ej.: no inventar datos de
   pedidos, plazos ni montos que no estén en el mail) y cómo firmar.
4) En el mensaje de usuario, pasale a Claude el remitente, asunto y cuerpo.
5) La respuesta (`response.content`) es una LISTA de bloques. Puede traer
   bloques que no son texto (Claude piensa antes de responder). Quedate solo
   con los de `type == "text"`.

Después, en main.py:
- El endpoint pasa a ser `async def` y llama a `await draft_reply(email)`
  SOLO cuando el mail es nuevo (los duplicados no gastan plata).
- La respuesta de un mail nuevo suma el campo "draft" con el borrador.
"""

import json

import anthropic

from models import Borrador, Email
from prompts import SYSTEM_PROMPT
from tools import TOOL_BUSCAR_PEDIDO, buscar_pedido

# Inicializo la instancia
cliente = anthropic.AsyncAnthropic()
SCHEMA_BORRADOR = {
    "type": "object",
    "properties": {
        "borrador": {"type": "string"},
        "notas_internas": {"type": "string"},
        "intencion": {
            "type": "string",
            "enum": [
                "estado_pedido",
                "devolucion",
                "reclamo",
                "consulta_general",
                "otro",
            ],
        },
        "urgencia": {"type": "string", "enum": ["alta", "media", "baja"]},
    },
    "required": ["borrador", "notas_internas", "intencion", "urgencia"],
    "additionalProperties": False,
}


async def llamar_a_claude(messages):
    return await cliente.messages.create(
        max_tokens=16000,
        model="claude-opus-5-5",
        system=SYSTEM_PROMPT,
        tools=[TOOL_BUSCAR_PEDIDO],
        messages=messages,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": SCHEMA_BORRADOR,
            }
        },
    )


async def draft_reply(email: Email) -> Borrador:
    messages = [
        {
            "role": "user",
            "content": (
                f"<email>\n"
                f"Remitente: {email.sender}\n"
                f"Asunto: {email.subject}\n"
                f"Cuerpo: {email.body}\n"
                f"</email>"
            ),
        }
    ]
    response = await llamar_a_claude(messages)

    while response.stop_reason == "tool_use":
        tools_results = []
        for block in response.content:
            if block.type == "tool_use":
                contenido = buscar_pedido(block.input["order_id"], email.sender)
                texto = (
                    json.dumps(contenido, ensure_ascii=False)
                    if contenido
                    else "No se encontró un pedido con ese número para este cliente."
                )
                tools_results += [
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": texto,
                    }
                ]
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tools_results},
        ]
        response = await llamar_a_claude(messages)

    # resultado = next(block for block in response.content if block.type == "text") esta es la forma cdo no tenemos un output estructurado
    resultado = Borrador.model_validate_json(
        next(block for block in response.content if block.type == "text").text
    )

    return resultado
