"""
Agente de mails — Paso 3a: la función que va a usar la tool
===========================================================

Antes de que Claude pueda "usar una herramienta", tiene que existir una
función de Python común que haga el trabajo. Esa es esta.

Implementá `buscar_pedido(order_id)`:
- Lee data/pedidos.csv (módulo `csv` de la librería estándar, como en el
  capítulo 16 del Crash Course; mirá `csv.DictReader`).
- Si encuentra el pedido, devolvé un dict con sus datos.
- Si no existe, devolvé None.
- Ojo: en el CSV todo es texto. ¿Qué pasa si te llaman con 1002 (int)
  en vez de "1002" (str)? Que funcione con los dos.

Corré este archivo solo para probar: uv run python tools.py
(no usa la API: es gratis)
"""

import csv
from pathlib import Path

RUTA_CSV = Path(__file__).parent / "data" / "pedidos.csv"


def buscar_pedido(order_id, email):
    with open(RUTA_CSV, "r", encoding="utf-8") as f:
        lector = csv.DictReader(f)
        for fila in lector:
            if fila["order_id"] == str(order_id) and fila["email"] == email:
                return fila

    return None


# Paso 3b (después de que pasen los asserts): la DEFINICIÓN de la tool,
# o sea, cómo se la describís a Claude. Claude no ve tu código: solo ve
# esto. Completá los TODO.
TOOL_BUSCAR_PEDIDO = {
    "name": "buscar_pedido",
    "description": (
        "Busca un pedido por su número y email si ambos coinciden devuelve sus datos: producto, estado "
        "(entregado, en_camino, demorado o preparando) y fecha estimada de entrega. "
        "Usala cuando el cliente pregunte por el estado, la entrega o una demora "
        "de un pedido y mencione un número de pedido. Si el pedido no existe, "
        "avisa que no se encontró. Si el mail no incluye un número de pedido, "
        "no lo inventes: el borrador tiene que pedírselo al cliente."
        "Devolvé solo el mail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Numero de pedido, ej. 10035"}
        },
        "required": ["order_id"],
    },
}


if __name__ == "__main__":
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
    print("¡Paso 3a completo! 🎉")
