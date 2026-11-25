import asyncio
import json

import pytest
from conftest import EXPORTER, HOST, OTHER_KEY, World, token
from mcp import Client
from mcp import types as t
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from voltrieve_doors import Gate, RateLimit, running

OPS = "/mcp/operations"
H = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


def rpc(method="tools/list", params=None, id="1"):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


class Session:
    def __init__(self, world, bearer, mode="auto", elicit=None, door="operations"):
        self.args = world, bearer, mode, elicit, door

    async def __aenter__(self):
        world, bearer, mode, elicit, door = self.args
        self.http = world.http(bearer)
        await self.http.__aenter__()
        self.client = Client(streamable_http_client(f"https://{HOST}/mcp/{door}", http_client=self.http), mode=mode,
                             elicitation_callback=elicit)
        return await self.client.__aenter__()

    async def __aexit__(self, *exc):
        await self.client.__aexit__(*exc)
        await self.http.__aexit__(*exc)


async def yes(ctx, params):
    return t.ElicitResult(action="accept", content={"ok": True})


async def no(ctx, params):
    return t.ElicitResult(action="decline")


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_a_client_of_either_generation_lists_and_calls(world, mode):
    async with Session(world, token(), mode) as c:
        assert c.protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
        names = {x.name for x in (await c.list_tools()).tools}
        assert "health" in names and "secret_tool" not in names
        result = await c.call_tool("health", {})
        assert json.loads(result.content[0].text) == {"by": "alice", "app": "Claude"}
    assert [(x.tool, x.app, x.result) for x in world.calls] == [("health", "Claude", "done")]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_a_tool_that_is_not_listed_is_refused_when_called(world, mode):
    async with Session(world, token(), mode) as c:
        result = await c.call_tool("secret_tool", {})
    assert result.is_error and result.content[0].text == "You may not use this tool."
    assert world.ran == [] and world.calls[-1].result == "denied"


async def test_a_person_is_not_asked_before_the_rules_are_checked(world):
    asked = []

    async def note(ctx, params):
        asked.append(params)
        return t.ElicitResult(action="accept", content={"ok": True})

    async with Session(world, token(roles=["Admin"]), elicit=note) as c:
        assert (await c.call_tool("careful", {"times": 1})).is_error
    assert not asked and not world.ran


async def test_a_yes_in_the_middle_of_a_call(world):
    async with Session(world, token(), elicit=yes) as c:
        assert not (await c.call_tool("careful", {"times": 2})).is_error
    assert world.ran == ["careful"] and [x.result for x in world.calls] == ["asked", "done"]


async def test_a_no_does_nothing(world):
    async with Session(world, token(), elicit=no) as c:
        assert (await c.call_tool("careful", {"times": 2})).is_error
    assert world.ran == []


@pytest.mark.parametrize("mode,elicit", [("auto", None), ("legacy", yes), ("legacy", None)])
async def test_a_client_that_cannot_ask_does_nothing(world, mode, elicit):
    async with Session(world, token(), mode, elicit) as c:
        with pytest.raises(MCPError):
            await c.call_tool("careful", {"times": 2})
    assert world.ran == []


BAD = {
    "another door": dict(aud="client-car", scp="car.read"),
    "the API": dict(aud="some-api"),
    "another tenant": dict(iss="https://login.microsoftonline.com/other/v2.0"),
    "a tenant that does not match": dict(tid="other"),
    "expired": dict(exp=1),
    "not yet": dict(nbf=9_999_999_999),
    "no oid": dict(oid=None),
    "another key": dict(key=OTHER_KEY),
    "an unknown key": dict(kid="zzz"),
}


@pytest.mark.parametrize("what", BAD)
async def test_a_bad_token_is_401(world, what):
    async with world.http(token(**BAD[what])) as c:
        r = await c.post(OPS, json=rpc(), headers=H)
    assert r.status_code == 401
    assert f'resource_metadata="https://{HOST}/.well-known/oauth-protected-resource/mcp/operations"' in r.headers["www-authenticate"]


@pytest.mark.parametrize("changes", [dict(scp="other"), dict(scp=""), dict(azp="app-9"), dict(azp=None), dict(roles=[]),
                                     dict(roles=None), dict(scp=None)])
async def test_a_token_without_the_rights_is_403(world, changes):
    async with world.http(token(**changes)) as c:
        assert (await c.post(OPS, json=rpc(), headers=H)).status_code == 403


async def test_no_token_points_to_the_sign_in(world):
    async with world.http() as c:
        r = await c.post(OPS, json=rpc(), headers=H)
        assert r.status_code == 401 and 'error=' not in r.headers["www-authenticate"]
        for door in ("operations", "car"):
            m = await c.get(f"/.well-known/oauth-protected-resource/mcp/{door}")
            assert m.json()["resource"] == f"https://{HOST}/mcp/{door}"
            assert m.json()["scopes_supported"] == [f"https://{HOST}/mcp/{door}/{door}.read"]


async def test_the_guard_answers_before_the_sdk(world):
    async with world.http(token()) as c:
        assert (await c.get(OPS, headers=H)).status_code == 405
        assert (await c.delete(OPS, headers=H)).status_code == 405
        assert (await c.post(OPS, json=rpc(), headers={**H, "origin": "https://evil.test"})).status_code == 403
        assert (await c.post(OPS, content=b"x" * 70_000, headers=H)).status_code == 413
        assert (await c.post(OPS, json=[rpc(), rpc()], headers=H)).status_code == 400
        assert (await c.post(OPS, content=b"[" * 60_000, headers=H)).status_code == 400
        deep = await c.post(OPS, content=b'{"jsonrpc":"2.0","a":' + b"[" * 40 + b"]" * 40 + b"}", headers=H)
        assert deep.status_code == 400 and "one JSON-RPC message" in deep.text
        assert (await c.post(OPS, content=b"not json", headers=H)).status_code == 400
        assert (await c.post("/mcp/unknown", json=rpc(), headers=H)).status_code == 204  # the rest of the app
        assert (await c.post(OPS + "/", json=rpc(), headers=H)).status_code == 204
    async with world.http(token(), host="evil.test") as c:
        assert (await c.post(OPS, json=rpc(), headers=H)).status_code == 421


async def test_limits():
    w = World(rate=RateLimit(3), gate=Gate(1))
    async with running(w.doors):
        async with w.http(token()) as c:
            old = {**H, "mcp-protocol-version": "2025-11-25"}
            codes = [(await c.post(OPS, json=rpc("tools/call", {"name": "health"}), headers=old)).status_code for _ in range(4)]
            assert codes == [200, 200, 200, 429]
            assert (await c.post(OPS, json=rpc(), headers=old)).status_code == 200  # connecting is not a call
            codes = [(await c.post(OPS, json=rpc(), headers=old)).status_code for _ in range(150)]
        assert codes[0] == 200 and codes[-1] == 429
        async with w.http(token(oid="bob")) as c:
            call = rpc("tools/call", {"name": "slow", "arguments": {}})
            h = {**H, "mcp-protocol-version": "2025-11-25"}
            two = await asyncio.gather(c.post(OPS, json=call, headers=h), c.post(OPS, json=call, headers=h))
            assert sorted(r.status_code for r in two) == [200, 429]
            assert (await c.post(OPS, json=call, headers=h)).status_code == 200  # the place is free again


async def test_one_person_cannot_take_every_place(world):
    call = rpc("tools/call", {"name": "slow", "arguments": {}})
    h = {**H, "mcp-protocol-version": "2025-11-25"}
    async with world.http(token()) as alice, world.http(token(oid="bob")) as bob:
        several = asyncio.gather(*[alice.post(OPS, json=call, headers=h) for _ in range(4)])
        await asyncio.sleep(0.1)
        assert (await bob.post(OPS, json=call, headers=h)).status_code == 200
        assert sorted(r.status_code for r in await several) == [200, 200, 429, 429]


@pytest.mark.parametrize("method", ["subscriptions/listen", "prompts/list", "resources/list", "logging/setLevel", "completion/complete", ""])
async def test_other_methods_are_refused(world, method):
    for version in ("2026-07-28", "2025-11-25"):
        async with world.http(token()) as c:
            r = await asyncio.wait_for(c.post(OPS, json=rpc(method), headers={**H, "mcp-protocol-version": version, "mcp-method": method}), 5)
        assert r.status_code == 404 and r.json()["error"]["message"] == "There is no such method here."
        assert r.json()["id"] == "1"


async def test_a_broken_body_counts_and_a_slow_one_ends(world, monkeypatch):
    monkeypatch.setattr("voltrieve_doors.door.BODY_SECONDS", 0.05)

    async def slow():
        yield b"{"
        await asyncio.sleep(5)

    async with world.http(token()) as c:
        codes = {(await c.post(OPS, content=b"x" * 70_000, headers=H)).status_code for _ in range(149)}
        assert codes == {413}
    sent = []

    async def receive():
        await asyncio.sleep(5)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": OPS, "headers": [(b"host", HOST.encode()), (b"authorization", f"Bearer {token()}".encode())]}
    await world.doors[0](scope, receive, send)
    assert sent[0]["status"] == 408
    await world.doors[0](scope, receive, send)
    assert sent[2]["status"] == 429


async def test_a_client_that_goes_away_frees_its_place(world):
    """With a server, since only that tells of a disconnect."""
    gone = asyncio.Event()
    body = json.dumps(rpc("tools/call", {"name": "slow", "arguments": {}})).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if messages:
            return messages.pop()
        gone.set()
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    scope = {"type": "http", "method": "POST", "path": OPS, "query_string": b"", "http_version": "1.1", "scheme": "https", "server": (HOST, 443),
             "headers": [(b"host", HOST.encode()), (b"authorization", f"Bearer {token()}".encode()), (b"mcp-protocol-version", b"2025-11-25"),
                         *[(k.encode(), v.encode()) for k, v in H.items()]]}
    await asyncio.wait_for(world.doors[0](scope, receive, send), 5)
    assert world.doors[0]._gate._inside.total() == 0


async def test_failures_say_nothing(world):
    async with Session(world, token()) as c:
        assert (await c.call_tool("breaks", {"text": "MARKERARG"})).content[0].text.endswith("That did not work. Try again later.")
        assert "Not today." in (await c.call_tool("refuses", {"text": "x"})).content[0].text
        assert (await c.call_tool("big", {})).is_error
    assert [x.result for x in world.calls] == ["failed", "refused", "refused"]


async def test_nothing_the_caller_sends_is_in_a_log_or_a_span(world, caplog):
    headers = {**H, "mcp-protocol-version": "2025-11-25", "user-agent": "MARKERAGENT", "x-other": "MARKERHEADER"}
    async with world.http(token(), headers=headers) as c:
        for body in (
            rpc("tools/call", {"name": "breaks", "arguments": {"text": "MARKERARG"}}, id="MARKERID"),
            rpc("tools/call", {"name": "breaks", "arguments": {"MARKERKEY": "MARKERARG"}}),
            rpc("tools/call", {"name": "MARKERTOOL", "arguments": {"a": "MARKERARG"}}),
            rpc("tools/call", {"name": ["MARKERTOOL"], "arguments": 3}),
            rpc("MARKERMETHOD", {"x": "MARKERPARAM"}),
            rpc("initialize", {"clientInfo": {"name": "MARKERCLIENT", "version": "1"}, "capabilities": {}, "protocolVersion": "MARKERVER"}),
            rpc("prompts/get", {"name": "MARKERPROMPT"}),
            rpc("resources/read", {"uri": "file:///MARKERURI"}),
            rpc("tools/list", {"_meta": {"MARKERMETA": "MARKERMETA", "progressToken": "MARKERPROGRESS"}}),
        ):
            await c.post(OPS, json=body, headers=headers)
        await c.post(OPS, content=b'{"MARKERBROKEN', headers=headers)
    async with world.http("MARKERTOKEN") as c:
        await c.post(OPS, json=rpc(), headers=H)
    async with Session(world, token()) as c:
        await c.call_tool("health", {"period": "MARKERARG"})
    spans = [s.to_json() for s in EXPORTER.get_finished_spans()]
    assert any("door.call" in s for s in spans)
    everything = "\n".join(spans) + "\n".join(str(r.__dict__) + r.getMessage() for r in caplog.records if not r.name.startswith("asyncio"))
    assert "MARKER" not in everything


async def test_an_application_needs_a_role_and_is_never_asked():
    w = World(applications=True)
    async with running(w.doors):
        async with Session(w, token(scp=None, azp="robot")) as c:
            names = {x.name for x in (await c.list_tools()).tools}
            assert "health" in names and "careful" not in names
            assert (await c.call_tool("careful", {"times": 1})).is_error
            assert not (await c.call_tool("health", {})).is_error
    assert w.ran == ["health"]


def test_an_address_must_be_a_path_without_a_slash_at_the_end():
    from dataclasses import replace

    from voltrieve_doors import Door

    good = World().doors[0].config
    for url in ("https://h/", "https://h/mcp/x/", "https://h/other", "/mcp/x"):
        with pytest.raises(ValueError):
            Door(replace(good, url=url), [], "")
