"""
Base de datos del perfil "tienda" (SQLite, viene con Python).

Es el ÚNICO archivo que sabe dónde están los datos. Para conectar la base real
de una empresa (Postgres, Supabase, MySQL...) se cambia `conectar_db()` por el
driver correspondiente; las tools siguen haciendo las mismas consultas.

La base de ejemplo se crea sola la primera vez desde data/pedidos.csv y la lista
POLITICAS de abajo. Para regenerarla: borrá data/tienda.db y volvé a correr.
"""

import csv
import sqlite3
from pathlib import Path

CARPETA_DATOS = Path(__file__).parent / "data"
RUTA_DB = CARPETA_DATOS / "tienda.db"

# Datos de ejemplo: políticas de una tienda ficticia
POLITICAS = [
    ("devoluciones", "Se aceptan devoluciones hasta 30 días después de la entrega, con el producto sin uso y en su caja. El reintegro se hace al mismo medio de pago en hasta 10 días hábiles desde que recibimos el producto."),
    ("cambios", "Los cambios de talle o color son gratis dentro de los 30 días de la entrega, sujetos a stock. El cliente envía el producto y despachamos el nuevo cuando lo recibimos."),
    ("envios", "Despachamos en 1 a 2 días hábiles. La entrega demora 2 a 5 días hábiles en AMBA y 4 a 8 en el resto del país. El envío es gratis en compras desde $80.000."),
    ("medios_de_pago", "Tarjetas de crédito y débito, transferencia bancaria y Mercado Pago. Con crédito hay hasta 3 cuotas sin interés."),
    ("garantia", "Todos los productos tienen 3 meses de garantía por fallas de fabricación. No cubre el desgaste por uso ni los daños por mal uso."),
]


def crear_db(ruta=RUTA_DB):
    """Crea las tablas y carga los datos de ejemplo."""
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE pedidos (
            order_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            producto TEXT NOT NULL,
            estado TEXT NOT NULL,
            fecha_estimada TEXT
        );
        CREATE TABLE politicas (
            tema TEXT PRIMARY KEY,
            texto TEXT NOT NULL
        );
    """)
    with open(CARPETA_DATOS / "pedidos.csv", encoding="utf-8") as f:
        filas = [tuple(fila.values()) for fila in csv.DictReader(f)]
    con.executemany("INSERT INTO pedidos VALUES (?, ?, ?, ?, ?)", filas)
    con.executemany("INSERT INTO politicas VALUES (?, ?)", POLITICAS)
    con.commit()
    con.close()


def conectar_db():
    """Devuelve una conexión a la base; la crea con datos de ejemplo si no existe."""
    if not RUTA_DB.exists():
        crear_db()
    con = sqlite3.connect(RUTA_DB)
    con.row_factory = sqlite3.Row  # cada fila se puede leer como dict
    return con
