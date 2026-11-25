"""The operations door, through a real MCP client and no network."""

import json
import logging

import httpx
import httpx2
import pytest
import respx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from voltrieve_doors import Caller, running

from app.doors.telemetry import PROBLEMS, HEALTH, TelemetryReader, explain_query, health_query, problems_query
from app.main import create_app
from app.settings import Settings
from app.usage import QUERY_URL, UsageUnavailable
from voltrieve_doors import Refused

HOST = "api.example.test"
WORKSPACE = "ws-1"
ADMIN = Caller("alice", "Claude", frozenset({"Dashboard.View"}), frozenset({"operations.read"}))


class Anyone:
    def __init__(self, who):
        self.who = who

    async def validate(self, authorization):
        return self.who


class Usage:
    async def insights(self, source):
        assert source == "real"
        return {"source": source, "rows": []}


def table(**columns):
    return {"tables": [{"columns": [{"name": c} for c in columns], "rows": [list(columns.values())]}]}


def make(who=ADMIN, **settings):
    app = create_app(Settings(tenant_id="t", api_client_id="a", mcp_api_url=f"https://{HOST}",
                              mcp_operations_client_id="ops", log_analytics_workspace_id=WORKSPACE, **settings), usage=Usage())

    async def token():
        return "t"

    app.state.telemetry = TelemetryReader(WORKSPACE, token)
    for door in app.state.doors:
        door._validator = Anyone(who)
    return app


async def call(app, name, arguments=None, mode="auto"):
    async with running(app.state.doors):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=f"https://{HOST}") as http:
            async with Client(streamable_http_client(f"https://{HOST}/mcp/operations", http_client=http), mode=mode) as c:
                names = [t.name for t in (await c.list_tools()).tools]
                result = await c.call_tool(name, arguments or {}) if name else None
    return names, result


def text(result):
    return result.content[0].text


NAMES = ["service_health", "recent_turns", "problem_turns", "explain_turn", "what_people_ask"]


async def test_the_door_exists_only_with_its_settings():
    for settings in ({}, {"mcp_api_url": "https://h"}, {"mcp_operations_client_id": "x"}):
        assert create_app(Settings(tenant_id="t", api_client_id="a", **settings)).state.doors == []


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_health(mode):
    app = make()
    with respx.mock:
        route = respx.post(QUERY_URL.format(id=WORKSPACE)).mock(return_value=httpx.Response(200, json=table(backend="loop", turns=3)))
        names, result = await call(app, "service_health", {"period": "1h"}, mode)
    assert names == NAMES and json.loads(text(result))["backends"] == [{"backend": "loop", "turns": 3}]
    assert "ago(1h)" in json.loads(route.calls[0].request.content)["query"]


@pytest.mark.parametrize("name", NAMES)
async def test_every_tool_needs_the_role_and_the_scope(name):
    for who in (Caller("bob", "Claude", frozenset({"Chat.User"}), frozenset({"operations.read"})),
                Caller("bob", "Claude", frozenset({"Dashboard.View"}), frozenset({"other"})),
                Caller("app", "application", frozenset({"Other"}), frozenset(), True)):
        names, result = await call(make(who), name, {"kind": "slow", "id": "x"})
        assert names == [] and result.is_error and text(result) == "You may not use this tool."


async def test_recent_turns_give_no_text_and_no_user():
    app = make()
    for i in range(30):
        app.state.turns.feed.publish({"turn_id": f"t{i}", "user": "MARKERUSER", "prompt": "MARKERPROMPT", "kind": "message",
                                      "backend": "loop", "new_field": "MARKERNEW", "country": "MARKERCOUNTRY"})
    _, result = await call(app, "recent_turns", {"count": 20})
    turns = json.loads(text(result))["turns"]
    assert len(turns) == 20 and turns[0]["turn_id"] == "t29" and "MARKER" not in text(result)
    for bad in (0, 21, "x"):
        assert (await call(make(), "recent_turns", {"count": bad}))[1].is_error


@pytest.mark.parametrize("bad", ["", 'x" or 1==1', "a b", "x\n", "x" * 65, "x;drop", "é"])
async def test_an_id_is_never_anything_else(bad):
    with pytest.raises(Refused):
        explain_query(bad)
    with respx.mock:  # no request is made
        assert (await call(make(), "explain_turn", {"id": bad}))[1].is_error


def test_periods_and_kinds_are_from_a_fixed_set():
    for bad in ("2h", "", "1h) | union *"):
        with pytest.raises(Refused):
            health_query(bad)
    with pytest.raises(Refused):
        problems_query("all", "1h")


def test_the_queries_give_no_user_no_text_and_no_address():
    for q in (HEALTH, *PROBLEMS.values(), explain_query("x")):
        last = q.split("| project")[-1]
        assert not any(word in last for word in ("voltrieve.user", "Target", "Data", "Url", "ClientIP", "correlation_id", "conversation_id"))


async def test_explain():
    with respx.mock:
        route = respx.post(QUERY_URL.format(id=WORKSPACE)).mock(side_effect=[
            httpx.Response(200, json={"tables": [{"columns": [{"name": "step"}], "rows": []}]}),
            httpx.Response(200, json=table(step="the whole turn", ok=True))])
        assert "Nothing is recorded" in text((await call(make(), "explain_turn", {"id": "abc"}))[1])
        assert json.loads(text((await call(make(), "explain_turn", {"id": "abc"}))[1]))["steps"][0]["step"] == "the whole turn"
        assert route.call_count == 2


@pytest.mark.parametrize("answer", [httpx.Response(500, text="MARKERERROR"), httpx.Response(200, json={"error": "MARKERERROR", "tables": []}),
                                    httpx.Response(200, text="no json"), httpx.ConnectError("MARKERERROR")])
async def test_a_failed_query_says_nothing(answer, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    with respx.mock:
        respx.post(QUERY_URL.format(id=WORKSPACE)).mock(side_effect=[answer])
        _, result = await call(make(), "service_health")
    assert result.is_error and "cannot be read right now" in text(result) and "MARKER" not in text(result) + caplog.text


async def test_the_same_question_is_asked_once():
    app = make()
    with respx.mock:
        route = respx.post(QUERY_URL.format(id=WORKSPACE)).mock(return_value=httpx.Response(200, json=table(backend="loop")))
        first = (await call(app, "service_health"))[1]
        assert not first.is_error and len(await app.state.telemetry.rows(health_query("24h"))) == 1
        assert route.call_count == 1


async def test_what_people_ask():
    app = make()
    assert json.loads(text((await call(app, "what_people_ask"))[1]))["source"] == "real"

    async def broken(source):
        raise UsageUnavailable("The usage insights are not available right now")

    app2 = make()
    app2.state.usage.insights = broken
    assert "not available" in text((await call(app2, "what_people_ask"))[1])
