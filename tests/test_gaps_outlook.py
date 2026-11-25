"""Proposed tests: outlook_client.py (fake Microsoft Graph over httpx.MockTransport, no network)."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from outlook_client import OutlookInbox, graph_headers, to_thread_messages

ME = "fran@outlook.com"


def msg(
    msg_id,
    when,
    sender,
    draft=False,
    body=None,
    conversation="conv-1",
    modified=None,
    unique=None,
    headers=None,
    subject="Report",
):
    m = {
        "id": msg_id,
        "conversationId": conversation,
        "receivedDateTime": when,
        "lastModifiedDateTime": modified or when,
        "isDraft": draft,
        "from": {"emailAddress": {"name": sender.split("@")[0], "address": sender}},
        "subject": subject,
        "body": {"contentType": "text", "content": body or f"text of {msg_id}"},
    }
    if unique is not False:
        m["uniqueBody"] = {
            "contentType": "text",
            "content": unique or body or f"text of {msg_id}",
        }
    if headers is not None:
        m["internetMessageHeaders"] = [
            {"name": k, "value": v} for k, v in headers.items()
        ]
    return m


class FakeGraph:
    """Records every request (method, path, params, headers) so tests can check what was asked."""

    def __init__(self, messages, me=None, fail=None):
        self.messages = {m["id"]: m for m in messages}
        self.me = me or {"mail": ME}
        self.fail = fail  # (method, path suffix) -> answers 500
        self.requests = []
        self.subscriptions = []

    def handler(self, request: httpx.Request):
        path = request.url.path.removeprefix("/v1.0")
        params = {
            k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()
        }
        self.requests.append((request.method, path, params, dict(request.headers)))
        if self.fail and request.method == self.fail[0] and path.endswith(self.fail[1]):
            return httpx.Response(500, json={"error": {"code": "serverError"}})
        if path == "/me":
            return httpx.Response(200, json=self.me)
        if path in ("/me/messages", "/me/mailFolders/inbox/messages"):
            query = params.get("$filter", "")
            if "conversationId eq" in query:
                cid = query.split("'")[1]
                return httpx.Response(
                    200,
                    json={
                        "value": [
                            m
                            for m in self.messages.values()
                            if m["conversationId"] == cid
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"conversationId": m["conversationId"]}
                        for m in self.messages.values()
                    ]
                },
            )
        if path.endswith("/createReply"):
            original = path.split("/")[3]
            draft_id = f"draft-of-{original}"
            self.messages[draft_id] = msg(
                draft_id,
                "2026-11-04T12:00:00Z",
                ME,
                draft=True,
                body=json.loads(request.content)["comment"],
            )
            return httpx.Response(201, json=self.messages[draft_id])
        if path.startswith("/me/messages/"):
            return httpx.Response(200, json=self.messages[path.split("/")[3]])
        if path == "/subscriptions" and request.method == "GET":
            return httpx.Response(200, json={"value": self.subscriptions})
        if path == "/subscriptions":
            body = json.loads(request.content)
            self.subscriptions.append({"id": "s1", **body})
            return httpx.Response(201, json={"id": "s1", **body})
        return httpx.Response(404)

    def listing(self):
        """The request that lists the recent emails (not the per-conversation ones)."""
        return next(
            r
            for r in self.requests
            if r[1].endswith("/messages")
            and "conversationId eq" not in r[2].get("$filter", "")
        )


def inbox_with(graph, filter_value="agente"):
    http = httpx.Client(
        base_url="https://graph.microsoft.com/v1.0",
        transport=httpx.MockTransport(graph.handler),
    )
    return OutlookInbox(http, filter_value)


def minutes_ago(n):
    return (datetime.now(timezone.utc) - timedelta(minutes=n)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ---------- what is asked to Graph ----------


def test_category_mode_asks_for_the_category_and_the_last_7_days():
    graph = FakeGraph([msg("m1", minutes_ago(5), "mark@x.com")])
    assert inbox_with(graph, "agente").list_pending() == ["m1"]
    _, path, params, _ = graph.listing()
    assert path == "/me/messages"
    f = params["$filter"]
    assert "categories/any(c:c eq 'agente')" in f and "inferenceClassification" not in f
    since = datetime.fromisoformat(
        f.split("receivedDateTime ge ")[1].split(" ")[0].replace("Z", "+00:00")
    )
    assert abs((datetime.now(timezone.utc) - since) - timedelta(days=7)) < timedelta(
        minutes=1
    )
    # With a category the user chose the emails: no extra request for the automated-mail headers
    assert not any(
        "internetMessageHeaders" in r[2].get("$select", "") for r in graph.requests
    )


def test_all_mail_mode_reads_the_focused_inbox_and_skips_automated():
    graph = FakeGraph(
        [
            msg(
                "m1",
                minutes_ago(5),
                "mark@x.com",
                conversation="c1",
                headers={"X-Mailer": "Outlook"},
            ),
            msg(
                "n1",
                minutes_ago(4),
                "news@shop.com",
                conversation="c2",
                headers={"List-Unsubscribe": "<mailto:u@x>"},
            ),
            msg(
                "n2", minutes_ago(3), "no-reply@bank.com", conversation="c3", headers={}
            ),
            msg(
                "n3",
                minutes_ago(2),
                "ana@x.com",
                conversation="c4",
                headers={"Auto-Submitted": "auto-replied"},
            ),
        ]
    )
    assert inbox_with(graph, "*").list_pending() == ["m1"]
    _, path, params, _ = graph.listing()
    assert path == "/me/mailFolders/inbox/messages"
    assert (
        "inferenceClassification eq 'focused'" in params["$filter"]
        and "categories" not in params["$filter"]
    )
    assert "receivedDateTime ge " in params["$filter"]


def test_graph_headers_lowercases_and_falls_back_to_the_sender():
    m = {
        "from": {"emailAddress": {"name": "Bank", "address": "no-reply@bank.com"}},
        "internetMessageHeaders": [{"name": "List-Unsubscribe", "value": "<x>"}],
    }
    assert graph_headers(m) == {
        "list-unsubscribe": "<x>",
        "from": "Bank <no-reply@bank.com>",
    }
    assert graph_headers({"from": None}) == {"from": " <>"}


# ---------- who sent it / what is a draft ----------


def test_my_address_is_compared_ignoring_case():
    graph = FakeGraph(
        [
            msg("m1", minutes_ago(9), "mark@x.com"),
            msg("m2", minutes_ago(5), "FRAN@Outlook.com"),
        ],
        me={"mail": "Fran@Outlook.COM"},
    )
    assert inbox_with(graph).list_pending() == []


def test_work_accounts_without_mail_use_the_user_principal_name():
    graph = FakeGraph(
        [
            msg("m1", minutes_ago(9), "mark@x.com"),
            msg("m2", minutes_ago(5), "fran@empresa.com"),
        ],
        me={"mail": None, "userPrincipalName": "Fran@empresa.com"},
    )
    assert inbox_with(graph).list_pending() == []


def test_a_draft_is_dated_by_its_last_edit():
    # Draft created after m1, the client writes again, then I keep editing that same draft
    graph = FakeGraph(
        [
            msg("m1", "2026-11-04T10:00:00Z", "mark@x.com"),
            msg(
                "d1",
                "2026-11-04T10:05:00Z",
                ME,
                draft=True,
                modified="2026-11-04T11:30:00Z",
            ),
            msg("m2", "2026-11-04T11:00:00Z", "mark@x.com"),
        ]
    )
    assert inbox_with(graph).list_pending() == []


def test_to_thread_messages_of_a_message_without_sender():
    [t] = to_thread_messages(
        [{"id": "x", "receivedDateTime": "2026-11-04T10:00:00Z", "from": None}], ME
    )
    assert (t.from_me, t.is_draft, t.date) == (
        False,
        False,
        datetime(2026, 11, 4, 10, tzinfo=timezone.utc).timestamp(),
    )


def test_several_conversations_each_decided_on_its_own():
    messages = []
    for i in range(3):
        messages += [
            msg(f"a{i}", minutes_ago(50), "mark@x.com", conversation=f"answered-{i}"),
            msg(f"r{i}", minutes_ago(40), ME, conversation=f"answered-{i}"),
            msg(f"p{i}", minutes_ago(30), "ana@x.com", conversation=f"pending-{i}"),
        ]
    assert sorted(inbox_with(FakeGraph(messages)).list_pending()) == ["p0", "p1", "p2"]


# ---------- read_email ----------


def test_read_email_asks_for_text_and_prefers_unique_body_without_quotes():
    graph = FakeGraph(
        [
            # Graph doesn't promise any order: newest first here
            msg(
                "m3",
                "2026-11-04T11:00:00Z",
                "mark@x.com",
                unique="Tuesday works\n\nOn Mon, Fran wrote:\n> Tuesday?",
                body="Tuesday works + FULL QUOTED HISTORY",
            ),
            msg("d1", "2026-11-04T10:40:00Z", ME, draft=True, body="Unsent draft text"),
            msg(
                "m2",
                "2026-11-04T10:30:00Z",
                ME,
                unique=False,
                body="Tuesday or Thursday?",
            ),  # no uniqueBody: fallback
            msg(
                "m1",
                "2026-11-04T10:00:00Z",
                "mark@x.com",
                unique="Can we meet?",
                body="Can we meet? + HISTORY",
            ),
        ]
    )
    inbox = inbox_with(graph)
    assert inbox.list_pending() == ["m3"]
    mail = inbox.read_email("m3")
    assert (mail.sender, mail.subject, mail.body) == (
        "mark@x.com",
        "Report",
        "Tuesday works",
    )
    assert mail.thread == (
        "From: mark <mark@x.com>\nDate: 2026-11-04T10:00:00Z\nCan we meet?\n---\n"
        "From: fran <fran@outlook.com>\nDate: 2026-11-04T10:30:00Z\nTuesday or Thursday?"
    )
    fetches = [
        r for r in graph.requests if r[0] == "GET" and r[1].startswith("/me/messages/")
    ]
    assert fetches and all(
        r[3].get("prefer") == 'outlook.body-content-type="text"' for r in fetches
    )


# ---------- create_draft / errors ----------


def test_create_draft_sends_the_text_as_the_reply_comment():
    graph = FakeGraph([msg("m1", minutes_ago(5), "mark@x.com")])
    draft = inbox_with(graph).create_draft("m1", "Great, Tuesday at 10.")
    assert graph.messages["draft-of-m1"]["body"]["content"] == "Great, Tuesday at 10."
    assert draft["id"] == "draft-of-m1"


def test_graph_errors_are_not_swallowed():
    with pytest.raises(httpx.HTTPStatusError):
        inbox_with(
            FakeGraph(
                [msg("m1", minutes_ago(5), "mark@x.com")], fail=("GET", "/me/messages")
            )
        ).list_pending()
    graph = FakeGraph(
        [msg("m1", minutes_ago(5), "mark@x.com")], fail=("POST", "/createReply")
    )
    with pytest.raises(httpx.HTTPStatusError):
        inbox_with(graph).create_draft("m1", "Hola")


def test_subscription_watches_the_inbox_and_expires_within_graphs_limit(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://agent.example.dev")
    monkeypatch.setenv("WEBHOOK_TOKEN", "secret")
    graph = FakeGraph([])
    expiry = inbox_with(graph).start_watch()
    [sub] = graph.subscriptions
    assert sub["resource"] == "me/mailFolders('inbox')/messages"
    left = expiry - datetime.now(timezone.utc)
    assert timedelta(days=1) < left <= timedelta(minutes=10080), (
        "Graph rejects mail subscriptions longer than 7 days"
    )

    failing = FakeGraph([], fail=("POST", "/subscriptions"))
    with pytest.raises(httpx.HTTPStatusError):
        inbox_with(failing).start_watch()


def test_read_email_falls_back_to_body_when_there_is_no_unique_body():
    graph = FakeGraph(
        [msg("m1", minutes_ago(5), "mark@x.com", unique=False, body="Only body")]
    )
    inbox = inbox_with(graph)
    inbox.list_pending()
    assert inbox.read_email("m1").body == "Only body"


def test_a_draft_without_sender_is_still_a_draft_not_a_client_email():
    graph = FakeGraph(
        [
            msg("m1", "2026-11-04T10:00:00Z", "mark@x.com"),
            {**msg("d1", "2026-11-04T10:05:00Z", ME, draft=True), "from": None},
        ]
    )
    assert inbox_with(graph).list_pending() == []
