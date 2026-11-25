"""
Tools Claude can use, and how they run.

Each tool has two parts:
- the DEFINITION (TOOL_...): the only thing Claude sees, to decide when to use it;
- the Python FUNCTION that does the work against the database (db.py).

Security rule: permissions are decided here, in code. For example, the email
sender is set by the code, not by Claude, so nobody can get someone else's
data even if they trick the model.

Tested in tests/test_tools.py (no API calls).
"""

import json
from contextlib import closing

from agent.db import connect_db


def find_order(order_id, email):
    """Returns the order (dict) only if the number AND the email match; otherwise None."""
    # People write " 1002" or "#1002", and "Customer@Example.com" is the same
    # mailbox as "customer@example.com". Only the way it's written is forgiven:
    # the number and the sender must still both match.
    order_id = str(order_id).strip().removeprefix("#").strip()
    with closing(connect_db()) as con:  # closes the connection on exit
        # The "?" are parameters: SQLite treats the value as data, never as SQL.
        # Never build the query with f-strings: that opens the door to SQL injection.
        row = con.execute(
            "SELECT * FROM orders WHERE order_id = ? AND lower(email) = ?",
            (order_id, email.lower()),
        ).fetchone()
    return dict(row) if row else None


def find_policy(topic):
    """Returns the text of a store policy, or None if it doesn't exist."""
    with closing(connect_db()) as con:  # closes the connection on exit
        row = con.execute(
            "SELECT text FROM policies WHERE topic = ?", (topic,)
        ).fetchone()
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
                "enum": [
                    "returns",
                    "exchanges",
                    "shipping",
                    "payment_methods",
                    "warranty",
                ],
            }
        },
        "required": ["topic"],
    },
}

TOOL_SEARCH_DOCS = {
    "name": "search_docs",
    "description": (
        "Searches the store's FAQ and returns the documents closest in meaning to the "
        "query. Use it for questions about how the store works that the other tools "
        "don't cover: opening hours, tracking an order on the website, invoices, gift "
        "cards, out-of-stock products, wholesale prices, physical stores or the "
        "customer's account. Don't use it for the status of an order (use find_order) "
        "or for returns, exchanges, shipping, payment methods or warranty (use "
        "find_policy). It returns the closest documents it has, so they may not answer "
        "the question: use one only if it actually does. If nothing is close enough, "
        "it says so."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "The customer's question as one short sentence, in the customer's "
                    "own words, e.g. 'Are you open on Saturdays?'"
                ),
            }
        },
        "required": ["query"],
    },
}


def _run_find_order(tool_input, email):
    order = find_order(tool_input["order_id"], email.sender)
    if order is None:
        return "No order with that number was found for this customer."
    return json.dumps(order, ensure_ascii=False)


def _run_find_policy(tool_input, email):
    return find_policy(tool_input["topic"]) or "There is no policy on that topic."


def _run_search_docs(tool_input, email):
    # Imported here, not at the top: the rest of the agent starts and runs without
    # the FAQ search, which needs the optional "rag" dependency group.
    from agent import rag

    # k and the minimum score are not taken from Claude: search_docs uses the values
    # chosen in rag.py from measured scores.
    texts = rag.search_docs(tool_input["query"], rag.faq_index(), rag.FAQ_DOCS)
    if not texts:
        return "No FAQ document answers that question."
    return "\n\n".join(texts)


# Tool name -> function that runs it. Receives what Claude asked for
# (`tool_input`) and the original email (`email`), and returns text for Claude.
EXECUTORS = {
    "find_order": _run_find_order,
    "find_policy": _run_find_policy,
    "search_docs": _run_search_docs,
}


def run_tool(name, tool_input, email):
    if name not in EXECUTORS:
        return f"The tool {name} does not exist."
    return EXECUTORS[name](tool_input, email)
