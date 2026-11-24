"""The hosted agent's handler with a fake model: event relay, outcome, caller check."""

import json

import httpx
import pytest
from voltrieve_agent.model import Completed, FunctionCall, TextDelta
from voltrieve_agent.private import pseudonym
from voltrieve_agent.tools import ServerTool

import main
from main import Runtime

OID = "11111111-2222-3333-4444-555555555555"

BODY = {
    "conversation_id": "conv_1",
    "turn_id": "turn_1",
    "input": "hello",
    "client_tools": [],
    "pending_outputs": [],
    "user": {"oid": "oid-anna", "username": "anna@example.com", "roles": ["TestDrive.Book"]},
    "correlation_id": "corr-1",
}


class ScriptedModel:
    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.calls = []
        self.recorded = []

    async def record(self, conversation, items):
        self.recorded.append((conversation, items))

    async def stream(self, *, instructions, conversation, input, tools, store=True):
        self.calls.append({"conversation": conversation, "input": input, "tools": tools, "store": store})
        for item in self.rounds.pop(0):
            yield item


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":"):
            continue
        lines = block.split("\n")
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


def client_for(runtime: Runtime) -> httpx.AsyncClient:
    host = main.create_host()
    main._runtime = runtime
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://agent")


@pytest.fixture(autouse=True)
def reset_runtime():
    yield
    main._runtime = None


async def invoke(runtime: Runtime, body=BODY, headers=None):
    async with client_for(runtime) as c:
        return await c.post("/invocations", json=body, headers=headers or {})


async def test_streams_events_then_outcome():
    model = ScriptedModel([TextDelta("Hi"), Completed({"total_tokens": 5})])
    r = await invoke(Runtime(model, [], allow_any_caller=True))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    types = [t for t, _ in events]
    assert types[-1] == "outcome" and "done" not in types
    assert [d["text"] for t, d in events if t == "delta"] == ["Hi"]
    assert events[-1][1] == {
        "status": "done",
        "pending_outputs": [],
        "client_call_ids": [],
        "usage": {"total_tokens": 5},
        "kept": None,
        "unrecorded": False,
        "private": False,
    }
    assert model.calls[0]["conversation"] == "conv_1"


async def test_client_tools_of_the_request_reach_the_model_and_pause_the_turn():
    tool = {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}
    model = ScriptedModel([FunctionCall("call_1", "read_file", "{}"), Completed({})])
    r = await invoke(Runtime(model, [], allow_any_caller=True), {**BODY, "client_tools": [tool]})
    events = parse_sse(r.text)
    assert "read_file" in [t["name"] for t in model.calls[0]["tools"]]
    paused = [d for t, d in events if t == "client_tool_calls"]
    assert paused[0]["calls"] == [{"call_id": "call_1", "name": "read_file", "arguments": "{}"}]
    assert events[-1][1]["status"] == "paused"
    assert events[-1][1]["client_call_ids"] == ["call_1"]


async def test_continuation_sends_pending_outputs_before_client_results():
    pending = [{"type": "function_call_output", "call_id": "s1", "output": "stock 3"}]
    results = [{"type": "function_call_output", "call_id": "c1", "output": "hi"}]
    model = ScriptedModel([TextDelta("ok"), Completed({})])
    await invoke(Runtime(model, [], allow_any_caller=True), {**BODY, "input": results, "pending_outputs": pending})
    assert model.calls[0]["input"] == [*pending, *results]


async def test_user_roles_gate_server_tools():
    seen = []

    async def book(args, user):
        seen.append(user)
        return "booked"

    tool = ServerTool("book", "Book", {"type": "object"}, book, required_role="TestDrive.Book")
    model = ScriptedModel(
        [FunctionCall("c1", "book", "{}"), Completed({})], [TextDelta("done"), Completed({})]
    )
    body = {**BODY, "user": {**BODY["user"], "roles": []}}
    r = await invoke(Runtime(model, [tool], allow_any_caller=True), body)
    assert seen == []
    assert any(d["step"] == "authorization_denied" for t, d in parse_sse(r.text) if t == "trace")

    model = ScriptedModel(
        [FunctionCall("c1", "book", "{}"), Completed({})], [TextDelta("done"), Completed({})]
    )
    await invoke(Runtime(model, [tool], allow_any_caller=True), BODY)
    assert seen[0].oid == "oid-anna" and "TestDrive.Book" in seen[0].roles


async def test_model_failure_is_an_error_event_and_a_failed_outcome():
    class Broken:
        def stream(self, **kwargs):
            raise RuntimeError("boom")

    events = parse_sse((await invoke(Runtime(Broken(), [], allow_any_caller=True))).text)
    assert [t for t, _ in events if t == "error"] == ["error"]
    assert events[-1][1]["status"] == "failed"


BAD_ATTACHMENT_BODIES = [
    {**BODY, "parts": "photo"},
    {**BODY, "parts": None},
    {**BODY, "parts": {"type": "input_image"}},
    {**BODY, "kept": []},
    {**BODY, "kept": "x"},
    {**BODY, "kept": {"items": [], "head": []}},
    {**BODY, "kept": {"items": {}, "head": [], "text": ""}},
    {**BODY, "kept": {"items": [], "head": "x", "text": ""}},
    {**BODY, "kept": {"items": [], "head": [], "text": 3}},
    {**BODY, "private": "yes"},
    {**BODY, "private": None},
]


@pytest.mark.parametrize(
    "body", [{"input": "x"}, {**BODY, "input": 3}, {**BODY, "user": {}}, *BAD_ATTACHMENT_BODIES]
)
async def test_malformed_body_is_400(body):
    r = await invoke(Runtime(ScriptedModel(), [], allow_any_caller=True), body)
    assert r.status_code == 400


def subject(oid):
    return {"x-ms-original-subject": f'oid="{oid}", tid="t", idtype="servicePrincipal"'}


async def test_caller_check_refuses_other_callers_and_allows_the_api():
    runtime = Runtime(ScriptedModel([TextDelta("a"), Completed({})]), [], OID)
    assert (await invoke(runtime)).status_code == 403
    assert (await invoke(runtime, headers=subject("not-a-guid"))).status_code == 403
    other = "99999999-2222-3333-4444-555555555555"
    assert (await invoke(runtime, headers=subject(other))).status_code == 403
    ok = await invoke(runtime, headers=subject(OID.upper()))
    assert ok.status_code == 200


async def test_check_fails_closed_without_an_allowed_caller():
    model = ScriptedModel([TextDelta("a"), Completed({})])
    assert (await invoke(Runtime(model, []), headers=subject(OID))).status_code == 403
    assert (await invoke(Runtime(model, [], ""), headers=subject(OID))).status_code == 403
    assert (await invoke(Runtime(model, [], "   "))).status_code == 403


async def test_allow_any_caller_is_an_explicit_opt_in():
    model = ScriptedModel([TextDelta("a"), Completed({})])
    assert (await invoke(Runtime(model, [], allow_any_caller=True))).status_code == 200


async def test_line_separators_in_text_survive_as_escapes():
    text = "a\u2028b\u2029c\u0085d"
    r = await invoke(Runtime(ScriptedModel([TextDelta(text), Completed({})]), [], allow_any_caller=True))
    for ch in "\u2028\u2029\u0085":
        assert ch not in r.text
    assert [d["text"] for t, d in parse_sse(r.text) if t == "delta"] == [text]


async def test_the_turn_is_logged_with_correlation_id_and_the_pseudonym_of_the_user(caplog):
    with caplog.at_level("INFO", logger="voltrieve.hosted"):
        await invoke(Runtime(ScriptedModel([TextDelta("a"), Completed({})]), [], allow_any_caller=True))
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn ")]
    assert "correlation_id corr-1" in line and line.endswith(f"user {pseudonym('oid-anna')}")
    assert "oid-anna" not in caplog.text and "anna@example.com" not in caplog.text


PHOTO = {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}
CALL = FunctionCall("call_1", "take_photo", "{}")
TAKE_PHOTO = [{"name": "take_photo", "description": "d", "parameters": {"type": "object"}}]


async def test_a_body_without_parts_and_kept_is_accepted():
    assert "parts" not in BODY and "kept" not in BODY
    model = ScriptedModel([TextDelta("Hi"), Completed({})])
    r = await invoke(Runtime(model, [], allow_any_caller=True))
    assert r.status_code == 200 and model.calls[0]["store"] is True


async def test_hosted_agent_passes_parts_and_kept_to_the_loop():
    body = {**BODY, "input": "Can I park?", "parts": [PHOTO], "client_tools": TAKE_PHOTO}
    model = ScriptedModel([CALL, Completed({})])
    events = parse_sse((await invoke(Runtime(model, [], allow_any_caller=True), body)).text)
    assert model.calls[0]["store"] is False and PHOTO in model.calls[0]["input"][0]["content"]
    outcome = events[-1][1]
    assert outcome["status"] == "paused" and outcome["unrecorded"] is False
    assert outcome["kept"]["items"][0]["content"][-1] == PHOTO and outcome["kept"]["text"] == ""
    # The continuation: the kept items go back in the body, as JSON.
    results = [{"type": "function_call_output", "call_id": "call_1", "output": "ok"}]
    body2 = {**BODY, "input": results, "client_tools": TAKE_PHOTO, "kept": outcome["kept"]}
    model2 = ScriptedModel([TextDelta("Yes."), Completed({})])
    events2 = parse_sse((await invoke(Runtime(model2, [], allow_any_caller=True), body2)).text)
    assert events2[-1][1]["status"] == "done"
    assert model2.calls[0]["store"] is False
    assert model2.calls[0]["input"][: len(outcome["kept"]["items"])] == outcome["kept"]["items"]


async def test_the_outcome_says_when_the_answer_could_not_be_recorded():
    class CannotRecord(ScriptedModel):
        async def record(self, conversation, items):
            raise RuntimeError("down")

    body = {**BODY, "parts": [PHOTO]}
    recorded = ScriptedModel([TextDelta("Yes."), Completed({})])
    events = parse_sse((await invoke(Runtime(recorded, [], allow_any_caller=True), body)).text)
    assert events[-1][1]["unrecorded"] is False and recorded.recorded[0][0] == "conv_1"
    failing = CannotRecord([TextDelta("Yes."), Completed({})])
    events = parse_sse((await invoke(Runtime(failing, [], allow_any_caller=True), body)).text)
    assert events[-1][1]["status"] == "done" and events[-1][1]["unrecorded"] is True


async def test_nothing_of_the_parts_or_kept_is_logged(caplog):
    kept = {"items": [PHOTO], "head": [], "text": "secret text"}
    body = {**BODY, "parts": [PHOTO], "kept": kept, "input": []}
    with caplog.at_level("DEBUG"):
        await invoke(Runtime(ScriptedModel([TextDelta("a"), Completed({})]), [], allow_any_caller=True), body)
        await invoke(Runtime(ScriptedModel(), [], allow_any_caller=True), {**BODY, "parts": "AAAA"})
    assert "AAAA" not in caplog.text and "secret text" not in caplog.text


@pytest.mark.parametrize(
    "extra",
    [
        {"private": True},
        {"parts": [PHOTO]},
        {"kept": {"items": [], "head": [], "text": ""}},
        {"parts": [PHOTO], "private": False},  # an attachment is private whatever the body says
        {"private": False},
        {},
    ],
)
async def test_a_failed_private_turn_logs_the_error_type_and_the_frames(caplog, monkeypatch, extra):
    async def fail(*args):
        raise RuntimeError("could not read SPARK")

    monkeypatch.setattr(main, "run_turn", fail)
    with caplog.at_level("DEBUG"):
        r = await invoke(Runtime(ScriptedModel(), [], allow_any_caller=True), {**BODY, **extra})
    assert parse_sse(r.text)[-1][1]["status"] == "failed"
    assert "Turn failed" in caplog.text and "RuntimeError" in caplog.text
    private = bool(extra.get("private") or extra.get("parts") or extra.get("kept"))
    assert ("SPARK" in caplog.text) is (not private) is ("Traceback" in caplog.text)
    assert "in fail" in caplog.text


async def test_the_private_flag_reaches_the_loop(caplog):
    class Echoing(ScriptedModel):
        async def stream(self, *, instructions, conversation, input, tools, store=True):
            raise RuntimeError(f"bad request: {input}")
            yield

    runtime = Runtime(Echoing(), [], allow_any_caller=True)
    await invoke(runtime, {**BODY, "input": "SPARK", "private": True})
    assert "Model stream failed: RuntimeError" in caplog.text and "SPARK" not in caplog.text
    await invoke(runtime, {**BODY, "input": "SPARK"})
    assert "bad request: SPARK" in caplog.text


def host_arguments(monkeypatch) -> dict:
    """The keyword arguments create_host passes to the agent server host's constructor."""
    import azure.ai.agentserver.invocations as invocations

    seen: dict = {}

    class FakeHost:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def invoke_handler(self, fn):
            return fn

    monkeypatch.setattr(invocations, "InvocationAgentServerHost", FakeHost)
    main.create_host()
    return seen


def test_the_host_gets_our_connection_string(monkeypatch):
    monkeypatch.setenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", "InstrumentationKey=x")
    seen = host_arguments(monkeypatch)
    assert seen["applicationinsights_connection_string"] == "InstrumentationKey=x"


def test_without_it_the_host_starts_without_telemetry(monkeypatch):
    monkeypatch.delenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", raising=False)
    assert host_arguments(monkeypatch)["applicationinsights_connection_string"] is None


def test_an_empty_connection_string_is_no_connection_string(monkeypatch):
    monkeypatch.setenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", "  ")
    assert host_arguments(monkeypatch)["applicationinsights_connection_string"] is None


JUNK = "not a connection string"
OURS = "InstrumentationKey=00000000-0000-0000-0000-000000000000"


def test_our_string_wins_over_a_broken_one_the_platform_left(monkeypatch):
    from azure.monitor.opentelemetry.exporter._connection_string_parser import ConnectionStringParser

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", JUNK)
    monkeypatch.setenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", OURS)
    host_arguments(monkeypatch)
    parser = ConnectionStringParser(OURS)
    assert parser.instrumentation_key == "00000000-0000-0000-0000-000000000000"


def test_without_our_string_a_broken_one_is_removed(monkeypatch):
    import os

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", JUNK)
    monkeypatch.delenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", raising=False)
    host_arguments(monkeypatch)
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in os.environ


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"name": "Anna A", "client": "web"}, ("Anna A", "web")),
        ({}, ("", "")),
        ({"name": None, "client": None}, ("", "")),
        ({"name": ["Anna"], "client": ["web"]}, ("", "")),
    ],
)
def test_the_name_and_the_client_are_optional(extra, expected):
    user = main.parse_turn({**BODY, "user": {**BODY["user"], **extra}}).user
    assert (user.name, user.client) == expected and user.oid == "oid-anna"


async def test_the_outcome_says_that_a_private_tool_was_used():
    async def handler(args, user):
        return "ok"

    tool = ServerTool("order", "d", {"type": "object"}, handler, private=True)
    model = ScriptedModel([FunctionCall("c1", "order", "{}"), Completed({})], [TextDelta("ok"), Completed({})])
    events = parse_sse((await invoke(Runtime(model, [tool], allow_any_caller=True))).text)
    assert events[-1][1]["private"] is True


@pytest.mark.parametrize("private", [True, False])
async def test_an_error_that_escapes_after_a_private_tool_logs_no_text(caplog, private):
    async def handler(args, user):
        return "SECRET 1"

    class Stream:
        def __init__(self, items, error=None):
            self.items, self.error = iter(items), error

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration:
                raise StopAsyncIteration

        async def aclose(self):
            if self.error:
                raise self.error

    class Model:
        streams = [Stream([FunctionCall("c1", "order", "{}")]), Stream([], RuntimeError("SECRET 1"))]

        def stream(self, **kwargs):
            return self.streams.pop(0)

    tool = ServerTool("order", "d", {"type": "object"}, handler, private=private)
    events = parse_sse((await invoke(Runtime(Model(), [tool], allow_any_caller=True))).text)
    assert events[-1][1]["status"] == "failed" and "Turn failed" in caplog.text
    assert ("SECRET 1" in caplog.text) is not private
