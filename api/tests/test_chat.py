import json

import pytest
from voltrieve_agent.events import AgentEvent, trace
from voltrieve_agent.loop import TurnOutcome

from tests.conftest import answer, pause, parse_sse

TOOL = {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}


async def chat(client, auth, body=None, **headers):
    return await client.post(
        "/chat", json=body or {"message": "hi"}, headers={**auth, **headers}
    )


async def test_healthz_needs_no_auth(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_no_token_is_401(client):
    r = await client.post("/chat", json={"message": "hi"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


async def test_no_role_is_403(client, make_token):
    r = await chat(client, {"Authorization": f"Bearer {make_token(roles=[])}"})
    assert r.status_code == 403


async def test_no_scope_is_403(client, make_token):
    r = await chat(client, {"Authorization": f"Bearer {make_token(scp='')}"})
    assert r.status_code == 403


async def test_sse_framing_and_order(client, auth, backend):
    backend.script = [answer("Hello", {"total_tokens": 7})]
    r = await chat(client, auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    types = [t for t, _ in events]
    assert types == ["trace", "trace", "status", "trace", "trace", "delta", "done"]
    steps = [d["step"] for t, d in events if t == "trace"]
    assert steps[:2] == ["token_validated", "backend_selected"]
    token_trace = events[0][1]
    assert token_trace["roles"] == ["Chat.User"] and "elapsed_ms" in token_trace
    assert events[1][1]["backend"] == "loop"
    done = events[-1][1]
    assert done["conversation_id"] == "conv_1" and done["backend"] == "loop"
    assert done["usage"] == {"total_tokens": 7}
    assert done["turn_id"] == backend.contexts[0].turn_id
    for t, d in events:
        if t == "trace":
            assert {"step", "component", "detail", "elapsed_ms"} <= d.keys()


async def test_second_turn_reuses_conversation(client, auth, backend):
    backend.script = [answer(), answer()]
    first = parse_sse((await chat(client, auth)).text)[-1][1]
    second = parse_sse(
        (await chat(client, auth, {"message": "again", "conversation_id": first["conversation_id"]})).text
    )[-1][1]
    assert second["conversation_id"] == first["conversation_id"] and backend.created == 1


async def test_foreign_or_unknown_conversation_is_403(client, auth, backend, make_token):
    backend.script = [answer()]
    conv = parse_sse((await chat(client, auth)).text)[-1][1]["conversation_id"]
    other = {"Authorization": f"Bearer {make_token(oid='oid-bob')}"}
    assert (await chat(client, other, {"message": "x", "conversation_id": conv})).status_code == 403
    assert (await chat(client, auth, {"message": "x", "conversation_id": "conv_nope"})).status_code == 403


async def test_a_foundry_outage_on_owner_lookup_is_503_not_403(client, auth, backend):
    async def down(conversation_id):
        raise ConnectionError("foundry is down")

    backend.get_conversation_owner = down
    r = await chat(client, auth, {"message": "x", "conversation_id": "conv_1"})
    assert r.status_code == 503
    assert "foundry" not in r.text.lower()


async def test_pause_then_continue(client, auth, backend):
    backend.script = [
        pause(("c1", "c2"), pending=[{"type": "function_call_output", "call_id": "s1", "output": "o"}]),
        answer("Done"),
    ]
    r = await chat(client, auth, {"message": "go", "client_tools": [TOOL]})
    events = parse_sse(r.text)
    assert events[-1][0] == "client_tool_calls"
    paused = events[-1][1]
    assert paused["conversation_id"] == "conv_1" and paused["turn_id"]
    assert [c["call_id"] for c in paused["calls"]] == ["c1", "c2"]
    assert "done" not in [t for t, _ in events]

    r2 = await client.post(
        "/chat/tool-results",
        headers=auth,
        json={
            "conversation_id": paused["conversation_id"],
            "turn_id": paused["turn_id"],
            "results": [
                {"call_id": "c2", "output": {"x": 1}},
                {"call_id": "c1", "output": "text"},
            ],
        },
    )
    events2 = parse_sse(r2.text)
    assert events2[-1][0] == "done" and events2[-1][1]["turn_id"] == paused["turn_id"]
    ctx = backend.contexts[1]
    assert ctx.turn_id == paused["turn_id"]
    assert [i["call_id"] for i in ctx.input] == ["c2", "c1"]
    assert ctx.input[0]["output"] == '{"x": 1}'
    assert ctx.pending_outputs[0]["call_id"] == "s1"
    assert [t.name for t in ctx.client_tools] == ["read_file"]


def results_body(paused, *call_ids):
    return {
        "conversation_id": paused["conversation_id"],
        "turn_id": paused["turn_id"],
        "results": [{"call_id": c, "output": "x"} for c in call_ids],
    }


async def paused_turn(client, auth, backend, **kw):
    backend.script.append(pause(**kw))
    return parse_sse((await chat(client, auth, {"message": "go", "client_tools": [TOOL]})).text)[-1][1]


async def test_mismatched_call_ids_are_422_and_turn_survives(client, auth, backend):
    paused = await paused_turn(client, auth, backend)
    for ids in [("other",), ("call_1", "extra"), ("call_1", "call_1")]:
        r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, *ids))
        assert r.status_code == 422
    backend.script.append(answer())
    r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][0] == "done"


async def test_foreign_oid_on_continue_gets_the_unknown_turn_answer(client, auth, backend, make_token):
    paused = await paused_turn(client, auth, backend)
    other = {"Authorization": f"Bearer {make_token(oid='oid-bob')}"}
    r = await client.post("/chat/tool-results", headers=other, json=results_body(paused, "call_1"))
    unknown = await client.post(
        "/chat/tool-results", headers=other, json=results_body({**paused, "turn_id": "nope"}, "call_1")
    )
    assert r.status_code == unknown.status_code == 200
    events = parse_sse(r.text)
    assert events[-1][1]["code"] == "turn_expired"
    assert [k for k, _ in events] == [k for k, _ in parse_sse(unknown.text)]
    assert len(backend.contexts) == 1 and backend.abandoned == []  # the owner's turn is untouched
    # ...and the owner can still finish it.
    backend.script = [answer("Done")]
    mine = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(mine.text)[-1][0] == "done"


async def test_unknown_turn_is_turn_expired_stream(client, auth):
    r = await client.post(
        "/chat/tool-results",
        headers=auth,
        json={"conversation_id": "conv_1", "turn_id": "nope", "results": [{"call_id": "a", "output": "x"}]},
    )
    events = parse_sse(r.text)
    assert r.status_code == 200 and events[-1] == (
        "error",
        {"code": "turn_expired", "message": "This turn is no longer available. Send the message again."},
    )
    assert events[0][1]["step"] == "token_validated"


async def test_unknown_turn_reports_the_requested_backend_when_it_is_a_real_one(client, auth):
    body = {"conversation_id": "conv_1", "turn_id": "nope", "results": [{"call_id": "a", "output": "x"}]}
    for header, expected in [("hosted", "hosted"), ("nonsense", "loop"), (None, "loop")]:
        headers = {**auth, **({"X-Agent-Backend": header} if header else {})}
        r = await client.post("/chat/tool-results", headers=headers, json=body)
        selected = next(d for k, d in parse_sse(r.text) if d.get("step") == "backend_selected")
        assert selected["detail"] == expected


async def test_expired_turn_reports_the_backend_of_the_turn(client, auth, backend, clock):
    paused = await paused_turn(client, auth, backend)
    clock.now += 15 * 60 + 1
    r = await client.post(
        "/chat/tool-results", headers={**auth, "X-Agent-Backend": "hosted"}, json=results_body(paused, "call_1")
    )
    selected = next(d for k, d in parse_sse(r.text) if d.get("step") == "backend_selected")
    assert selected["detail"] == "loop"


async def test_expired_turn_is_closed_and_reported(client, auth, backend, clock):
    paused = await paused_turn(
        client, auth, backend, pending=[{"type": "function_call_output", "call_id": "s", "output": "o"}]
    )
    clock.now += 15 * 60 + 1
    r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][1]["code"] == "turn_expired"
    conv, outputs = backend.abandoned[0]
    assert conv == "conv_1"
    assert [o["call_id"] for o in outputs] == ["s", "call_1"]
    assert outputs[1]["output"] == "the client did not return a result"
    assert len(backend.contexts) == 1


async def test_new_message_on_paused_conversation_closes_it_first(client, auth, backend):
    paused = await paused_turn(
        client, auth, backend, pending=[{"type": "function_call_output", "call_id": "s", "output": "o"}]
    )
    backend.script.append(answer())
    r = await chat(client, auth, {"message": "never mind", "conversation_id": paused["conversation_id"]})
    events = parse_sse(r.text)
    assert events[-1][0] == "done"
    assert [o["call_id"] for o in backend.abandoned[0][1]] == ["s", "call_1"]
    # The old turn is gone.
    r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][1]["code"] == "turn_expired"


async def test_expired_pause_closed_when_conversation_touched(client, auth, backend, clock):
    paused = await paused_turn(client, auth, backend)
    clock.now += 3600
    backend.script.append(answer())
    await chat(client, auth, {"message": "hello again", "conversation_id": paused["conversation_id"]})
    assert backend.abandoned and backend.abandoned[0][0] == "conv_1"


async def test_backend_exception_gives_generic_error(client, auth, backend):
    async def boom(ctx, emit):
        raise RuntimeError("secret internal detail")

    backend.script = [boom]
    events = parse_sse((await chat(client, auth)).text)
    assert events[-1] == ("error", {"code": "internal_error", "message": "The request could not be completed."})
    assert "secret" not in json.dumps(events)


async def test_disconnect_does_not_stop_the_turn(app, auth, backend):
    import asyncio

    release = asyncio.Event()

    async def slow(ctx, emit):
        await release.wait()
        return await pause()(ctx, emit)

    backend.script = [slow]
    from app.turns import Job
    from app.auth import Claims

    stream = app.state.turns.start(
        Job(Claims("oid-anna", "a", ["Chat.User"]), "loop", None, "t1", "hi", [])
    )
    await anext(stream)  # first event, then the client goes away
    await stream.aclose()
    release.set()
    for _ in range(50):
        if app.state.store.get("t1"):
            break
        await asyncio.sleep(0.01)
    assert app.state.store.get("t1") is not None


@pytest.mark.parametrize(
    "parameters",
    [
        {"type": "object", "properties": {"a": {"$ref": "https://evil.example/schema.json"}}},
        {"type": "object", "$defs": {"x": {"type": "string"}}, "properties": {"a": {"$ref": "#/$defs/x"}}},
        {"type": "object", "allOf": [{"$dynamicRef": "#meta"}]},
        {"type": "object", "properties": {"a": {"items": [{"$ref": "#"}]}}},
    ],
)
async def test_client_tool_schemas_with_references_are_422(client, auth, parameters):
    tool = {"name": "read_file", "description": "d", "parameters": parameters}
    r = await chat(client, auth, {"message": "x", "client_tools": [tool]})
    assert r.status_code == 422 and "$ref" in r.text


@pytest.mark.parametrize(
    "tools",
    [
        [{**TOOL, "name": "search_voltrieve_docs"}],
        [{**TOOL, "name": "bad name!"}],
        [TOOL, TOOL],
        [{**TOOL, "name": f"t{i}"} for i in range(17)],
        [{**TOOL, "description": "x" * 1500, "name": f"t{i}"} for i in range(12)],
        [{**TOOL, "parameters": {"type": "nonsense"}}],
    ],
)
async def test_bad_client_tools_are_422(client, auth, backend, tools):
    r = await chat(client, auth, {"message": "hi", "client_tools": tools})
    assert r.status_code == 422 and not backend.contexts


async def test_oversized_body_is_413(client, auth):
    r = await chat(client, auth, {"message": "x" * 70_000})
    assert r.status_code == 413


async def test_unknown_and_unconfigured_backend_is_400(client, auth):
    r = await client.post(
        "/chat", json={"message": "hi"}, headers={**auth, "X-Agent-Backend": "nonsense"}
    )
    assert r.status_code == 400 and "Unknown backend" in r.json()["detail"]
    r = await client.post(
        "/chat", json={"message": "hi"}, headers={**auth, "X-Agent-Backend": "prompt"}
    )
    assert r.status_code == 400 and "not configured" in r.json()["detail"]


async def test_cors(client):
    ok = await client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,x-agent-backend",
        },
    )
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    bad = await client.options(
        "/chat",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_correlation_id(client, auth, backend):
    backend.script = [answer(), answer()]
    r = await chat(client, auth, **{"X-Correlation-Id": "abc.123"})
    assert r.headers["x-correlation-id"] == "abc.123"
    assert backend.contexts[0].correlation_id == "abc.123"
    r = await chat(client, auth, **{"X-Correlation-Id": "bad id!\t"})
    generated = r.headers["x-correlation-id"]
    assert generated != "bad id!" and len(generated) == 36
    assert backend.contexts[1].correlation_id == generated
    assert (await client.get("/healthz")).headers["x-correlation-id"]


async def test_owner_survives_restart_via_foundry_metadata(app, auth, backend, make_token, validator):
    import httpx

    from app.backends import BackendRegistry
    from app.main import create_app
    from app.settings import Settings
    from app.turns import TurnStore

    backend.owners["conv_old"] = "oid-anna"
    settings = Settings()
    fresh = create_app(settings, validator, BackendRegistry(settings, {"loop": lambda s: backend}), TurnStore())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://t") as c:
        bob = {"Authorization": f"Bearer {make_token(oid='oid-bob')}"}
        assert (await chat(c, bob, {"message": "x", "conversation_id": "conv_old"})).status_code == 403
        assert (await chat(c, auth, {"message": "x", "conversation_id": "conv_missing"})).status_code == 403
        backend.script = [answer()]
        r = await chat(c, auth, {"message": "x", "conversation_id": "conv_old"})
        assert parse_sse(r.text)[-1][0] == "done"
    assert backend.repaired == ["conv_old"]  # first touch in this process
    backend.script = [answer()]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fresh), base_url="http://t") as c:
        await chat(c, auth, {"message": "x", "conversation_id": "conv_old"})
    assert backend.repaired == ["conv_old"]  # not again


async def test_repair_failure_blocks_the_turn(app, auth, backend, clock):
    backend.owners["conv_old"] = "oid-anna"
    backend.repair_error = RuntimeError("boom")
    r = await chat(auth=auth, client=app.state and __import__("httpx").AsyncClient(
        transport=__import__("httpx").ASGITransport(app=app), base_url="http://t"
    ), body={"message": "x", "conversation_id": "conv_old"})
    events = parse_sse(r.text)
    assert events[-1][1]["code"] == "conversation_recovery_failed"
    assert not backend.contexts


async def test_failed_close_marks_conversation_for_repair(client, auth, backend):
    paused = await paused_turn(client, auth, backend)
    backend.abandon_error = RuntimeError("down")
    backend.script.append(answer())
    r = await chat(client, auth, {"message": "again", "conversation_id": paused["conversation_id"]})
    assert parse_sse(r.text)[-1][0] == "done"
    # The failed close leaves the conversation dirty, so the same turn repairs it first.
    assert backend.repaired == [paused["conversation_id"]]


async def test_failed_continuation_marks_conversation_for_repair(client, auth, backend):
    paused = await paused_turn(client, auth, backend)

    async def boom(ctx, emit):
        raise RuntimeError("x")

    backend.script.append(boom)
    r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][1]["code"] == "internal_error"
    backend.script.append(answer())
    await chat(client, auth, {"message": "again", "conversation_id": paused["conversation_id"]})
    assert backend.repaired == [paused["conversation_id"]]


async def test_running_turn_gives_409(client, auth, backend):
    import asyncio

    gate = asyncio.Event()

    async def slow(ctx, emit):
        await gate.wait()
        return await answer()(ctx, emit)

    backend.script = [answer()]
    conv = parse_sse((await chat(client, auth)).text)[-1][1]["conversation_id"]
    backend.script = [slow, answer()]
    first = asyncio.create_task(chat(client, auth, {"message": "slow", "conversation_id": conv}))
    await asyncio.sleep(0.05)
    r = await chat(client, auth, {"message": "second", "conversation_id": conv})
    assert r.status_code == 409 and r.json()["detail"] == "turn_in_progress"
    gate.set()
    await first
    r = await chat(client, auth, {"message": "third", "conversation_id": conv})
    assert r.status_code == 200


async def test_usage_covers_the_whole_turn(client, auth, backend):
    backend.script = [pause(usage={"total_tokens": 3, "d": {"x": 1}}), answer(usage={"total_tokens": 4, "d": {"x": 2}})]
    paused = parse_sse((await chat(client, auth, {"message": "go", "client_tools": [TOOL]})).text)[-1][1]
    r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][1]["usage"] == {"total_tokens": 7, "d": {"x": 3}}


async def test_expired_pauses_are_evicted_and_conversation_repaired(app, client, auth, backend, clock):
    paused = await paused_turn(client, auth, backend)
    clock.now += 3600
    assert app.state.store.evict_expired() == 1
    assert app.state.store.get(paused["turn_id"]) is None
    backend.script.append(answer())
    await chat(client, auth, {"message": "x", "conversation_id": paused["conversation_id"]})
    assert backend.repaired == [paused["conversation_id"]]


def test_conversation_map_is_a_bounded_lru():
    from app.turns import TurnStore

    store = TurnStore(max_conversations=2)
    store.register_conversation("a", "o")
    store.register_conversation("b", "o")
    store.conversation_owner("a")
    store.register_conversation("c", "o")
    assert store.conversation_owner("b") is None and store.conversation_owner("a") == "o"


async def test_keepalive_comment_when_idle(client, auth, backend, monkeypatch):
    import asyncio

    from app import turns

    monkeypatch.setattr(turns, "KEEPALIVE_SECONDS", 0.02)

    async def slow(ctx, emit):
        await asyncio.sleep(0.1)
        return await answer()(ctx, emit)

    backend.script = [slow]
    r = await chat(client, auth)
    assert ": keep-alive\n\n" in r.text
    assert parse_sse(r.text.replace(": keep-alive\n\n", ""))[-1][0] == "done"


async def test_lifespan_closes_backends_and_drains(app, backend, auth):
    async with app.router.lifespan_context(app):
        backend.script = [answer()]
        async with __import__("httpx").AsyncClient(
            transport=__import__("httpx").ASGITransport(app=app), base_url="http://t"
        ) as c:
            await chat(c, auth)
    assert backend.closed is True


async def test_chunked_oversized_body_is_413(client, auth):
    async def body():
        for _ in range(80):
            yield b"x" * 1024

    r = await client.post("/chat", content=body(), headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 413


async def test_deeply_nested_schema_is_422(client, auth, backend):
    nested = {"type": "object"}
    for _ in range(40):
        nested = {"type": "object", "properties": {"a": nested}}
    r = await chat(client, auth, {"message": "hi", "client_tools": [{**TOOL, "parameters": nested}]})
    assert r.status_code == 422
    raw = '{"message":"hi","client_tools":[{"name":"t","description":"d","parameters":' + "[" * 20000 + "]" * 20000 + "}]}"
    r = await client.post("/chat", content=raw[:60000], headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 422


async def test_non_ascii_header_does_not_crash(client, auth):
    r = await client.get("/healthz", headers={"X-Correlation-Id": "caf\u00e9".encode("latin-1")})
    assert r.status_code == 200 and len(r.headers["x-correlation-id"]) == 36


async def test_new_conversation_is_running_from_creation(client, auth, backend):
    import asyncio

    gate = asyncio.Event()

    async def slow(ctx, emit):
        await gate.wait()
        return await answer()(ctx, emit)

    backend.script = [slow]
    first = asyncio.create_task(chat(client, auth))
    for _ in range(100):
        if backend.created:
            break
        await asyncio.sleep(0.01)
    r = await chat(client, auth, {"message": "second", "conversation_id": "conv_1"})
    assert r.status_code == 409
    gate.set()
    await first
    backend.script = [answer()]
    assert (await chat(client, auth, {"message": "third", "conversation_id": "conv_1"})).status_code == 200


async def test_turn_expired_job_does_not_touch_running_marker(client, auth, backend):
    import asyncio

    gate = asyncio.Event()

    async def slow(ctx, emit):
        await gate.wait()
        return await answer()(ctx, emit)

    backend.script = [answer()]
    conv = parse_sse((await chat(client, auth)).text)[-1][1]["conversation_id"]
    backend.script = [slow]
    running = asyncio.create_task(chat(client, auth, {"message": "slow", "conversation_id": conv}))
    await asyncio.sleep(0.05)
    r = await client.post(
        "/chat/tool-results",
        headers=auth,
        json={"conversation_id": conv, "turn_id": "nope", "results": [{"call_id": "a", "output": "x"}]},
    )
    assert parse_sse(r.text)[-1][1]["code"] == "turn_expired"
    r = await chat(client, auth, {"message": "second", "conversation_id": conv})
    assert r.status_code == 409
    gate.set()
    await running


CAR_BODY = {"message": "hi", "context": {"car": {"model": "Voltrieve Spark", "registration": "ABC123", "odometer_km": 4000}}}


@pytest.fixture
def three_backends(validator, backend, clock):
    from app.attachments.store import AttachmentStore
    from app.backends import BackendRegistry
    from app.main import create_app
    from app.settings import Settings
    from app.turns import TurnStore

    settings = Settings(cors_origins="http://localhost:5173", cli_client_id="cli", car_client_id="car-app")
    registry = BackendRegistry(settings, {name: (lambda s: backend) for name in ("loop", "prompt", "hosted")})
    return create_app(
        settings, validator, registry, TurnStore(clock=clock), attachments=AttachmentStore(clock=clock)
    )


@pytest.fixture
async def multi(three_backends):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=three_backends), base_url="http://test") as c:
        yield c


@pytest.mark.parametrize("name", ["loop", "prompt", "hosted"])
async def test_the_test_header_marks_the_conversation_as_test(multi, auth, backend, name):
    backend.script = [answer()]
    await chat(multi, auth, CAR_BODY, **{"X-Agent-Backend": name, "X-Voltrieve-Test": "1"})
    assert backend.metadata == [
        {"owner_oid": "oid-anna", "client": "test", "backend": name, "car_model": "spark"}
    ]


async def test_without_the_header_the_client_comes_from_azp(multi, auth, backend, make_token):
    backend.script = [answer(), answer()]
    await chat(multi, auth)
    await chat(multi, {"Authorization": f"Bearer {make_token(azp='car-app')}"})
    assert [m["client"] for m in backend.metadata] == ["cli", "car"]
    assert "car_model" not in backend.metadata[0]


@pytest.mark.parametrize("value", ["0", "true", "yes", "", "11"])
async def test_any_other_value_of_the_test_header_is_ignored(multi, auth, backend, value):
    backend.script = [answer()]
    await chat(multi, auth, **{"X-Voltrieve-Test": value})
    assert backend.metadata[0]["client"] == "cli"


async def test_a_later_message_does_not_create_metadata_again(multi, auth, backend):
    backend.script = [answer(), answer()]
    first = parse_sse((await chat(multi, auth)).text)[-1][1]
    await chat(multi, auth, {"message": "more", "conversation_id": first["conversation_id"]})
    assert len(backend.metadata) == 1


async def test_the_turn_span_and_the_feed_carry_the_client(multi, auth, backend, spans):
    backend.script = [answer()]
    await chat(multi, auth, CAR_BODY, **{"X-Voltrieve-Test": "1"})
    await multi._transport.app.state.turns.drain()
    attrs = next(s for s in spans.get_finished_spans() if s.name == "chat.turn").attributes
    assert attrs["voltrieve.client"] == "test"
    feed = multi._transport.app.state.turns.feed.recent(False)
    assert [e["client"] for e in feed] == ["test"]


async def test_the_test_header_is_not_a_cors_header(multi):
    r = await multi.options(
        "/chat",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "x-voltrieve-test"},
    )
    assert r.status_code == 400


async def test_the_test_header_also_marks_the_tool_results_round(multi, auth, backend, spans):
    paused = await paused_turn(multi, auth, backend)
    backend.script.append(answer())
    r = await multi.post(
        "/chat/tool-results", headers={**auth, "X-Voltrieve-Test": "1"}, json=results_body(paused, "call_1")
    )
    assert parse_sse(r.text)[-1][0] == "done"
    await multi._transport.app.state.turns.drain()
    rounds = [s.attributes for s in spans.get_finished_spans() if s.attributes.get("voltrieve.kind") == "tool_results"]
    assert [a["voltrieve.client"] for a in rounds] == ["test"]
    assert len(backend.metadata) == 1  # only the first message created a conversation


def private_pause(private=True):
    async def run(ctx, emit):
        outcome = await pause(pending=[{"type": "function_call_output", "call_id": "s1", "output": "SECRET 1"}])(ctx, emit)
        outcome.private = private
        return outcome

    return run


async def quoting_failure(ctx, emit):
    raise RuntimeError(f"bad request: {ctx.pending_outputs} {ctx.input}")


@pytest.mark.parametrize("private", [True, False])
async def test_a_turn_that_goes_on_after_a_private_tool_is_private(app, client, auth, backend, caplog, private):
    backend.script = [private_pause(private), pause(), quoting_failure]
    paused = parse_sse((await chat(client, auth, {"message": "go", "client_tools": [TOOL]})).text)[-1][1]
    assert app.state.store.get(paused["turn_id"]).private is private
    with caplog.at_level("DEBUG"):
        for _ in range(2):  # it stays private over a second pause
            r = await client.post("/chat/tool-results", headers=auth, json=results_body(paused, "call_1"))
    assert parse_sse(r.text)[-1][1]["code"] == "internal_error"
    assert [c.private for c in backend.contexts] == [False, private, private]
    assert "Turn failed" in caplog.text and ("bad request" in caplog.text) is not private


async def test_a_failed_close_after_a_private_tool_logs_no_text(client, auth, backend, caplog):
    backend.script = [private_pause(), answer()]
    paused = parse_sse((await chat(client, auth, {"message": "go", "client_tools": [TOOL]})).text)[-1][1]
    backend.abandon_error = RuntimeError("SECRET 1")
    await chat(client, auth, {"message": "new", "conversation_id": paused["conversation_id"]})
    assert "Could not close the abandoned turn" in caplog.text and "SECRET 1" not in caplog.text


async def test_a_failed_source_lookup_after_a_private_tool_logs_no_text(app, client, auth, backend, caplog):
    class Broken:
        async def resolve(self, text):
            raise RuntimeError(f"cannot look up {text}")

    async def run(ctx, emit):
        await emit(AgentEvent("delta", {"text": "SECRET 1"}))
        return TurnOutcome("done", private=True)

    app.state.turns.sources = Broken()
    backend.script = [run]
    assert parse_sse((await chat(client, auth)).text)[-1][0] == "done"
    assert "Source lookup failed" in caplog.text and "SECRET 1" not in caplog.text


async def test_the_client_header_is_not_a_cors_header(multi):
    r = await multi.options(
        "/chat",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "x-voltrieve-client"},
    )
    assert r.status_code == 400


async def test_the_client_header_does_not_change_the_usage_count(multi, auth, backend):
    backend.script = [answer()]
    await chat(multi, auth, **{"X-Voltrieve-Client": "car"})
    assert backend.metadata[0]["client"] == "cli"
    assert backend.contexts[0].user.client == "car"


async def test_the_name_reaches_the_tools(multi, backend, make_token):
    backend.script = [answer(), answer()]
    await chat(multi, {"Authorization": f"Bearer {make_token(name='Anna A')}"})
    await chat(multi, {"Authorization": f"Bearer {make_token()}"})
    assert [c.user.name for c in backend.contexts] == ["Anna A", "anna@example.com"]


@pytest.mark.parametrize("private", [True, False])
async def test_the_rest_of_a_request_after_a_private_tool_is_quiet(client, auth, backend, caplog, private):
    async def run(ctx, emit):
        mark = {"private": True} if private else {}
        await emit(trace("server_tool_called", "tools", "order", tool="order", **mark))
        await emit(trace("server_tool_called", "tools", "check_stock", tool="check_stock", arguments="SECRET 1"))
        await emit(trace("server_tool_returned", "tools", "check_stock", tool="check_stock", output_preview="SECRET 2"))
        raise RuntimeError("SECRET 3")

    backend.script = [run]
    with caplog.at_level("DEBUG"):
        r = await chat(client, auth)
    assert parse_sse(r.text)[-1][1]["code"] == "internal_error" and "Turn failed: RuntimeError" in caplog.text or not private
    assert [f"SECRET {n}" in caplog.text for n in (1, 2, 3)] == [not private] * 3


async def test_a_pause_after_a_private_tool_is_private_whatever_the_outcome_says(app, client, auth, backend):
    async def run(ctx, emit):
        await emit(trace("server_tool_called", "tools", "order", tool="order", private=True))
        return await pause()(ctx, emit)

    backend.script = [run]
    paused = parse_sse((await chat(client, auth, {"message": "go", "client_tools": [TOOL]})).text)[-1][1]
    assert app.state.store.get(paused["turn_id"]).private


@pytest.mark.parametrize("name", ["prepare_car_order", "place_car_order"])
async def test_a_client_cannot_declare_an_order_tool(client, auth, backend, name):
    r = await chat(client, auth, {"message": "hi", "client_tools": [{**TOOL, "name": name}]})
    assert r.status_code == 422 and name in r.text and backend.contexts == []
