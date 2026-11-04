"""
Tools Claude can use, and how they run.

Each tool has two parts:
- the DEFINITION (TOOL_...): the only thing Claude sees, to decide when to use it;
- the Python FUNCTION that does the work against the database (db.py).

Security rule: permissions are decided here, in code. For example, the email
sender is set by the code, not by Claude, so nobody can get someone else's
data even if they trick the model.

Run it with: uv run python tools.py   (doesn't call the API: it's free)
"""

import json
from contextlib import closing

from db import connect_db


def find_order(order_id, email):
    """Returns the order (dict) only if the number AND the email match; otherwise None."""
    with closing(connect_db()) as con:  # closes the connection on exit
        # The "?" are parameters: SQLite treats the value as data, never as SQL.
        # Never build the query with f-strings: that opens the door to SQL injection.
        row = con.execute(
            "SELECT * FROM orders WHERE order_id = ? AND email = ?",
            (str(order_id), email),
        ).fetchone()
    return dict(row) if row else None


def find_policy(topic):
    """Returns the text of a store policy, or None if it doesn't exist."""
    with closing(connect_db()) as con:  # closes the connection on exit
        row = con.execute("SELECT text FROM policies WHERE topic = ?", (topic,)).fetchone()
    return row["text"] if row else None


TOOL_FIND_ORDER = {
    "name": "find_order",
    "description": (
        "Looks up one of the sender's orders by number and returns the product, status "
        "(delivered, in_transit, delayed or preparing) and estimated delivery date. "
        "Use it when the customer asks about the status, delivery or delay of an order "
        "and mentions the order number. If the email has no number, don't make one up: "
        "the draft must ask for it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order number, e.g. 1002"}
        },
        "required": ["order_id"],
    },
}

TOOL_FIND_POLICY = {
    "name": "find_policy",
    "description": (
        "Returns the official text of a store policy. Always use it when the customer "
        "asks about returns, exchanges, shipping, payment methods or warranty, instead "
        "of answering from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["returns", "exchanges", "shipping", "payment_methods", "warranty"],
            }
        },
        "required": ["topic"],
    },
}


def _run_find_order(tool_input, email):
    order = find_order(tool_input["order_id"], email.sender)
    if order is None:
        return "No order with that number was found for this customer."
    return json.dumps(order, ensure_ascii=False)


def _run_find_policy(tool_input, email):
    return find_policy(tool_input["topic"]) or "There is no policy on that topic."


# Tool name -> function that runs it. Receives what Claude asked for
# (`tool_input`) and the original email (`email`), and returns text for Claude.
EXECUTORS = {
    "find_order": _run_find_order,
    "find_policy": _run_find_policy,
}


def run_tool(name, tool_input, email):
    if name not in EXECUTORS:
        return f"The tool {name} does not exist."
    return EXECUTORS[name](tool_input, email)


if __name__ == "__main__":
    from models import Email

    order = find_order("1002", "customer@example.com")
    assert order is not None
    assert order["product"] == "Running sneakers"
    assert order["status"] == "in_transit"
    assert find_order(1002, "customer@example.com") == order, "Must work with int and str"
    assert find_order("9999", "customer@example.com") is None
    assert find_order("1003", "customer@example.com") is None, (
        "Must not return someone else's order"
    )
    assert find_order("1002' OR '1'='1", "customer@example.com") is None, (
        "A SQL injection attempt must not return data"
    )

    assert "30 days" in find_policy("returns")
    assert find_policy("made_up") is None

    mail = Email(message_id="t", sender="customer@example.com", subject="", body="")
    assert "sneakers" in run_tool("find_order", {"order_id": "1002"}, mail)
    assert "No order" in run_tool("find_order", {"order_id": "1003"}, mail)
    assert "free on orders" in run_tool("find_policy", {"topic": "shipping"}, mail)
    assert "does not exist" in run_tool("delete_everything", {}, mail)
    print("Database tools OK")
