"""
Procesa la bandeja: por cada mail nuevo con la etiqueta "agente", Claude
redacta la respuesta y queda como BORRADOR en el mismo hilo de Gmail.
Nunca envía nada: vos revisás el borrador y lo mandás.

Correlo con: uv run --env-file .env python procesar.py
(usa la API de Claude: cada mail nuevo es una llamada paga)

El perfil (prompt + tools) se elige con --perfil; por defecto "freelance":
    uv run --env-file .env python procesar.py --perfil tienda

Para que quede corriendo solo y revise la bandeja cada N minutos (Ctrl+C lo corta):
    uv run --env-file .env python procesar.py --cada 5
"""

import argparse
import asyncio
import json
from pathlib import Path

import anthropic

from drafter import draft_reply
from gmail_client import conectar, crear_borrador, leer_mail, listar_pendientes
from perfiles import PERFILES

# IDs ya procesados, para no crear dos borradores del mismo mail
# (el mail sigue "sin leer" hasta que lo abras, así que la búsqueda lo vuelve a traer)
RUTA_PROCESADOS = Path(__file__).parent / "procesados.json"


def cargar_procesados():
    if RUTA_PROCESADOS.exists():
        return set(json.loads(RUTA_PROCESADOS.read_text()))
    return set()


def guardar_procesados(procesados):
    RUTA_PROCESADOS.write_text(json.dumps(sorted(procesados), indent=1))


async def main(perfil):
    servicio = conectar()
    procesados = cargar_procesados()
    nuevos = [i for i in listar_pendientes(servicio) if i not in procesados]
    print(f"Perfil: {perfil} | Mails nuevos para procesar: {len(nuevos)}")

    for mensaje_id in nuevos:
        mail = leer_mail(servicio, mensaje_id)
        print(f"\n— De: {mail.sender} | Asunto: {mail.subject}")
        try:
            draft = await draft_reply(mail, perfil)
        except anthropic.APIError as e:
            # No se marca como procesado: la próxima corrida lo reintenta
            print(f"  Error de la API de Claude, queda para reintentar: {e}")
            continue

        crear_borrador(servicio, mensaje_id, draft.borrador)
        procesados.add(mensaje_id)
        guardar_procesados(procesados)
        print(f"  Intención: {draft.intencion} | Urgencia: {draft.urgencia}")
        print(f"  Notas internas: {draft.notas_internas}")
        print("  ✅ Borrador creado en Gmail")


async def vigilar(perfil, minutos):
    """Corre main() para siempre, con una pausa de `minutos` entre vuelta y vuelta."""
    while True:
        try:
            await main(perfil)
        except Exception as e:
            # Acá sí se atrapa cualquier error (ej. se cortó internet y falla Gmail):
            # el proceso tiene que seguir vivo y reintentar en la próxima vuelta.
            print(f"Error en esta vuelta, se reintenta en {minutos} min: {e!r}")
        print(f"\nPróxima revisión en {minutos} min (Ctrl+C para cortar)")
        await asyncio.sleep(minutos * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--perfil", choices=PERFILES, default="freelance")
    parser.add_argument("--cada", type=int, help="minutos entre revisiones; sin esto corre una sola vez")
    args = parser.parse_args()
    if args.cada:
        asyncio.run(vigilar(args.perfil, args.cada))
    else:
        asyncio.run(main(args.perfil))
