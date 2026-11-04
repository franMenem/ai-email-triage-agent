"""
OutlookInbox against a fake Microsoft Graph (httpx.MockTransport): which emails
need a reply, the thread, the draft call and the subscription. No network.
"""

import json
from urllib.parse import parse_qs, urlparse

import httpx

from outlook_client import OutlookInbox

ME = "fran@outlook.com"


def msg(msg_id, when, sender, draft=False, body=None, conversation="conv-1"):
    return {
        "id": msg_id,
        "conversationId": conversation,
        "receivedDateTime": when,
        "lastModifiedDateTime": when,
        "isDraft": draft,
        "from": {"emailAddress": {"name": sender.split("@")[0], "address": sender}},
        "subject": "Report",
        "uniqueBody": {"contentType": "text", "content": body or f"text of {msg_id}"},
        "body": {"contentType": "text", "content": body or f"text of {msg_id}"},
    }


class FakeGraph:
    def __init__(self, messages, subscriptions=()):
        self.messages = {m["id"]: m for m in messages}
        self.subscriptions = list(subscriptions)
        self.calls = []

    def handler(self, request: httpx.Request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path.removeprefix("/v1.0")
        params = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if path == "/me":
            return httpx.Response(200, json={"mail": ME})
        if path in ("/me/messages", "/me/mailFolders/inbox/messages"):
            query = params.get("$filter", "")
            if "conversationId eq" in query:
                cid = query.split("'")[1]
                return httpx.Response(200, json={"value": [m for m in self.messages.values() if m["conversationId"] == cid]})
            return httpx.Response(200, json={"value": [{"conversationId": m["conversationId"]} for m in self.messages.values()]})
        if path.startswith("/me/messages/") and path.endswith("/createReply"):
            original = path.split("/")[3]
            draft_id = f"draft-of-{original}"
            self.messages[draft_id] = msg(draft_id, "2026-11-04T12:00:00Z", ME, draft=True,
                                          body=json.loads(request.content)["comment"])
            return httpx.Response(201, json=self.messages[draft_id])
        if path.startswith("/me/messages/"):
            return httpx.Response(200, json=self.messages[path.split("/")[3]])
        if path == "/subscriptions" and request.method == "GET":
            return httpx.Response(200, json={"value": self.subscriptions})
        if path == "/subscriptions":
            body = json.loads(request.content)
            self.subscriptions.append({"id": "s1", **body})
            return httpx.Response(201, json={"id": "s1", **body})
        if path.startswith("/subscriptions/"):
            return httpx.Response(200, json={"id": "s1", **json.loads(request.content)})
        return httpx.Response(404)


def inbox_with(graph, filter_value="agente"):
    http = httpx.Client(base_url="https://graph.microsoft.com/v1.0", transport=httpx.MockTransport(graph.handler))
    return OutlookInbox(http, filter_value)


def test_the_latest_client_email_needs_a_reply_and_gets_a_draft():
    graph = FakeGraph([
        msg("m1", "2026-11-04T10:00:00Z", "mark@x.com", body="Can we meet?"),
        msg("m2", "2026-11-04T10:30:00Z", ME, body="Tuesday or Thursday?"),
        msg("m3", "2026-11-04T11:00:00Z", "mark@x.com", body="Tuesday works"),
    ])
    inbox = inbox_with(graph)
    assert inbox.list_pending() == ["m3"]

    mail = inbox.read_email("m3")
    assert (mail.sender, mail.body) == ("mark@x.com", "Tuesday works")
    assert mail.thread.index("Can we meet?") < mail.thread.index("Tuesday or Thursday?")

    inbox.create_draft("m3", "Great, Tuesday at 10.")
    assert ("POST", "/v1.0/me/messages/m3/createReply") in graph.calls
    assert inbox_with(graph).list_pending() == [], "Already drafted: nothing to do"


def test_nothing_pending_after_you_replied():
    graph = FakeGraph([
        msg("m1", "2026-11-04T10:00:00Z", "mark@x.com"),
        msg("m2", "2026-11-04T10:30:00Z", ME),
    ])
    assert inbox_with(graph).list_pending() == []


def test_subscription_is_created_then_renewed(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://agent.example.dev/")
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    graph = FakeGraph([])
    inbox = inbox_with(graph)

    inbox.start_watch()
    [sub] = graph.subscriptions
    assert sub["notificationUrl"] == "https://agent.example.dev/outlook-webhook"
    assert sub["clientState"] == "secret"
    assert sub["changeType"] == "created"

    inbox.start_watch()
    assert ("PATCH", "/v1.0/subscriptions/s1") in graph.calls, "The existing one is renewed, not duplicated"
    assert len(graph.subscriptions) == 1
