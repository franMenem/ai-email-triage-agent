"""
Tools que Claude puede usar, y cómo se ejecutan.

Cada tool tiene dos partes:
- la DEFINICIÓN (TOOL_...): lo único que ve Claude, para decidir cuándo usarla;
- la FUNCIÓN de Python que hace el trabajo contra la base (db.py).

Regla de seguridad: los permisos se deciden acá, en código. Por ejemplo, el
remitente del mail lo pone el código, no Claude, así que nadie puede pedir
datos de otra persona aunque engañe al modelo.

Probalo con: uv run python tools.py   (no usa la API: es gratis)
"""

import json
from contextlib import closing

from db import conectar_db


def buscar_pedido(order_id, email):
    """Devuelve el pedido (dict) solo si el número Y el email coinciden; si no, None."""
    with closing(conectar_db()) as con:  # cierra la conexión al salir
        # Los "?" son parámetros: SQLite trata el valor como dato, nunca como SQL.
        # Nunca armar la consulta con f-strings: eso abre la puerta a inyección SQL.
        fila = con.execute(
            "SELECT * FROM pedidos WHERE order_id = ? AND email = ?",
            (str(order_id), email),
        ).fetchone()
    return dict(fila) if fila else None


def buscar_politica(tema):
    """Devuelve el texto de una política de la tienda, o None si no existe."""
    with closing(conectar_db()) as con:  # cierra la conexión al salir
        fila = con.execute("SELECT texto FROM politicas WHERE tema = ?", (tema,)).fetchone()
    return fila["texto"] if fila else None


TOOL_BUSCAR_PEDIDO = {
    "name": "buscar_pedido",
    "description": (
        "Busca un pedido del remitente por su número y devuelve producto, estado "
        "(entregado, en_camino, demorado o preparando) y fecha estimada de entrega. "
        "Usala cuando el cliente pregunte por el estado, la entrega o una demora "
        "de un pedido y mencione el número. Si el mail no trae el número, no lo "
        "inventes: el borrador tiene que pedírselo."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Número de pedido, ej. 1002"}
        },
        "required": ["order_id"],
    },
}

TOOL_BUSCAR_POLITICA = {
    "name": "buscar_politica",
    "description": (
        "Devuelve el texto oficial de una política de la tienda. Usala siempre que "
        "el cliente pregunte por devoluciones, cambios, envíos, medios de pago o "
        "garantía, en vez de responder de memoria."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tema": {
                "type": "string",
                "enum": ["devoluciones", "cambios", "envios", "medios_de_pago", "garantia"],
            }
        },
        "required": ["tema"],
    },
}


def _ejecutar_buscar_pedido(entrada, email):
    pedido = buscar_pedido(entrada["order_id"], email.sender)
    if pedido is None:
        return "No se encontró un pedido con ese número para este cliente."
    return json.dumps(pedido, ensure_ascii=False)


def _ejecutar_buscar_politica(entrada, email):
    return buscar_politica(entrada["tema"]) or "No hay una política cargada sobre ese tema."


# Nombre de la tool -> función que la ejecuta. Recibe lo que pidió Claude
# (`entrada`) y el mail original (`email`), y devuelve texto para Claude.
EJECUTORES = {
    "buscar_pedido": _ejecutar_buscar_pedido,
    "buscar_politica": _ejecutar_buscar_politica,
}


def ejecutar_tool(nombre, entrada, email):
    if nombre not in EJECUTORES:
        return f"La herramienta {nombre} no existe."
    return EJECUTORES[nombre](entrada, email)


if __name__ == "__main__":
    from models import Email

    pedido = buscar_pedido("1002", "cliente@ejemplo.com")
    assert pedido is not None
    assert pedido["producto"] == "Zapatillas running"
    assert pedido["estado"] == "en_camino"
    assert buscar_pedido(1002, "cliente@ejemplo.com") == pedido, (
        "Tiene que andar con int y con str"
    )
    assert buscar_pedido("9999", "cliente@ejemplo.com") is None
    assert buscar_pedido("1003", "cliente@ejemplo.com") is None, (
        "No puede devolver pedidos ajenos"
    )
    assert buscar_pedido("1002' OR '1'='1", "cliente@ejemplo.com") is None, (
        "Un intento de inyección SQL no puede devolver datos"
    )

    assert "30 días" in buscar_politica("devoluciones")
    assert buscar_politica("inventada") is None

    mail = Email(message_id="t", sender="cliente@ejemplo.com", subject="", body="")
    assert "Zapatillas" in ejecutar_tool("buscar_pedido", {"order_id": "1002"}, mail)
    assert "No se encontró" in ejecutar_tool("buscar_pedido", {"order_id": "1003"}, mail)
    assert "envío es gratis" in ejecutar_tool("buscar_politica", {"tema": "envios"}, mail)
    assert "no existe" in ejecutar_tool("borrar_todo", {}, mail)
    print("¡Tools con base de datos OK! 🎉")
