"""Database tools: permissions in code, SQL injection, unknown tools. No API calls."""

from models import Email
from tools import find_order, find_policy, run_tool

MAIL = Email(message_id="t", sender="customer@example.com", subject="", body="")


def test_find_order_returns_the_senders_order():
    order = find_order("1002", "customer@example.com")
    assert order["product"] == "Running sneakers"
    assert order["status"] == "in_transit"
    assert find_order(1002, "customer@example.com") == order, "Must work with int and str"


def test_find_order_never_returns_someone_elses_order():
    assert find_order("9999", "customer@example.com") is None
    assert find_order("1003", "customer@example.com") is None


def test_find_order_resists_sql_injection():
    assert find_order("1002' OR '1'='1", "customer@example.com") is None


def test_find_policy():
    assert "30 days" in find_policy("returns")
    assert find_policy("made_up") is None


def test_run_tool_dispatches_and_rejects_unknown_tools():
    assert "sneakers" in run_tool("find_order", {"order_id": "1002"}, MAIL)
    assert "No order" in run_tool("find_order", {"order_id": "1003"}, MAIL)
    assert "free on orders" in run_tool("find_policy", {"topic": "shipping"}, MAIL)
    assert "does not exist" in run_tool("delete_everything", {}, MAIL)
