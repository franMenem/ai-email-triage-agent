"""
Database for the "store" profile (SQLite, included with Python).

This is the ONLY file that knows where the data lives. To connect a company's
real database (Postgres, Supabase, MySQL...), change `connect_db()` to use that
driver; the tools keep running the same queries.

The sample database is created automatically the first time, from
data/orders.csv and the POLICIES list below. To rebuild it, delete
data/store.db and run again.
"""

import csv
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "store.db"

# Sample data: policies of a fictional online store
POLICIES = [
    ("returns", "Returns are accepted up to 30 days after delivery, with the product unused and in its box. The refund goes back to the original payment method within 10 business days after we receive the product."),
    ("exchanges", "Size or color exchanges are free within 30 days of delivery, subject to stock. The customer sends the product back and we ship the new one once we receive it."),
    ("shipping", "Orders ship within 1 to 2 business days. Delivery takes 2 to 5 business days in the metro area and 4 to 8 elsewhere. Shipping is free on orders over $100."),
    ("payment_methods", "Credit and debit cards, bank transfer and Mercado Pago. Credit cards allow up to 3 interest-free installments."),
    ("warranty", "All products have a 3-month warranty against manufacturing defects. It does not cover normal wear or damage from misuse."),
]


def create_db(path=DB_PATH):
    """Creates the tables and loads the sample data."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE orders (
            order_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            product TEXT NOT NULL,
            status TEXT NOT NULL,
            estimated_delivery TEXT
        );
        CREATE TABLE policies (
            topic TEXT PRIMARY KEY,
            text TEXT NOT NULL
        );
    """)
    with open(DATA_DIR / "orders.csv", encoding="utf-8") as f:
        rows = [tuple(row.values()) for row in csv.DictReader(f)]
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)", rows)
    con.executemany("INSERT INTO policies VALUES (?, ?)", POLICIES)
    con.commit()
    con.close()


def connect_db():
    """Returns a connection to the database; creates it with sample data if missing."""
    if not DB_PATH.exists():
        create_db()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row  # each row can be read like a dict
    return con
