"""One scripted conversation per scenario, run through the real API and each real backend with
a fake model. Every backend must produce the same event shapes (spec sections 8.1 and 14).

The `hosted` backend runs against an in-process stand-in for the agent; see `_hosted`.
"""

import copy
import importlib.util
import json
import logging
import os
from pathlib import Path

os.environ.setdefault("TENANT_ID", "00000000-0000-0000-0000-0000000000aa")
os.environ.setdefault("API_CLIENT_ID", "00000000-0000-0000-0000-0000000000cc")

import httpx  # noqa: E402
import pytest  # noqa: E402
from app import attachments  # noqa: E402
from app.attachments import Part  # noqa: E402
from app.auth import Claims  # noqa: E402
from app.backends import BackendRegistry  # noqa: E402
from app.backends.loop import LoopBackend  # noqa: E402
from app.backends.prompt import PromptBackend  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.turns import TurnStore  # noqa: E402
from voltrieve_agent.model import Completed, FunctionCall, OutputItem, TextDelta  # noqa: E402
from voltrieve_agent.server_tools import KB_SEARCH_TOOLS, build_server_tools  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative_path: str):
    """Import a script that is not an installed package (agents are separate projects)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAKE_NOISE = {"name": "make_noise", "description": "Ring", "parameters": {"type": "object"}}
BOOK = '{"model": "atlas", "city": "Göteborg", "date": "2030-01-05"}'
BACKENDS = ["loop", "prompt", "hosted"]


class ScriptedModel:
    """Each call of stream() plays the next round. A round that is an Exception is raised.

    Like the real client, an unstored call also gives back the items of its function calls. The
    calls and the recordings are kept, and so is what went to the hosted agent and came back.
    """

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.calls = []
        self.recorded = []
        self.wire = []  # the request body and the outcome of each hosted call
        self.agent = None

    def as_agent(self, name):
        """A prompt agent with the same script. Its calls are kept here, with its name."""
        agent = copy.copy(self)
        agent.agent = name
        return agent

    async def stream(self, *, instructions, conversation, input, tools, store=True):
        # Through JSON: what was sent at the time, and it must be possible to send.
        sent = json.loads(json.dumps(input))
        self.calls.append({"agent": self.agent, "conversation": conversation, "input": sent, "store": store})
        round_ = self.rounds.pop(0)
        if isinstance(round_, Exception):
            raise round_
        for item in round_:
            yield item
        for call in [i for i in round_ if isinstance(i, FunctionCall) and not store]:
            yield OutputItem(function_call(call.call_id, call.name, call.arguments))

    async def record(self, conversation, items):
        self.recorded.append((conversation, json.loads(json.dumps(items))))


def function_call(call_id, name, arguments):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}


class FakeConversations:
    async def create(self, metadata=None):
        return type("Conv", (), {"id": "conv_contract"})()


class FakeOpenAI:
    conversations = FakeConversations()


def build_backend(name: str, model: ScriptedModel):
    server_tools = build_server_tools(None, None)  # handlers never run: no scenario reaches them
    if name == "loop":
        return LoopBackend(model, FakeOpenAI(), server_tools)
    if name == "prompt":
        # The agents share one script, since a scenario uses one of them.
        agents = [model.as_agent(n) for n in ("cli", "web", "car")]
        return PromptBackend(*agents, FakeOpenAI(), [t for t in server_tools if t.name not in KB_SEARCH_TOOLS])
    return _hosted(model, server_tools)


def _hosted(model, server_tools):
    """The hosted backend against the real hosted agent code, minus the Foundry gateway.

    The stand-in runs what agents/hosted/main.py runs for a request: `parse_turn` on the body and
    `stream_turn` with the scripted model, which answers with SSE ending in an `outcome` event.
    An httpx.MockTransport carries it, so the real HostedBackend relay runs too.
    """
    from app.backends.hosted import HostedBackend

    hosted = load_module("hosted_main", "agents/hosted/main.py")
    runtime = hosted.Runtime(model, server_tools, allow_any_caller=True)

    async def agent(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        turn = hosted.parse_turn(body)
        assert turn is not None, "the API sent a body the hosted agent rejects"
        wire = b"".join([chunk async for chunk in hosted.stream_turn(runtime, turn)])
        model.wire.append((body, parse_sse(wire.decode())[-1][1]))
        return httpx.Response(200, content=wire, headers={"x-agent-session-id": "sess-contract"})

    async def token(scope: str) -> str:
        return "token"

    http = httpx.AsyncClient(transport=httpx.MockTransport(agent))
    return HostedBackend(FakeOpenAI(), http, token, "https://foundry.invalid/api/projects/p", "voltrieve-hosted")


def api_for(name: str, model: ScriptedModel, roles: tuple[str, ...]):
    class Validator:
        async def validate(self, authorization):
            return Claims("oid-contract", "contract@example.invalid", list(roles))

    backend = build_backend(name, model)
    settings = Settings(agent_backend=name, log_level="WARNING")
    return create_app(settings, Validator(), BackendRegistry(settings, {name: lambda s: backend}), TurnStore())


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        events.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return events


def shape(events) -> list[str]:
    """Event types with runs of the same type collapsed (the number of deltas varies)."""
    out: list[str] = []
    for kind, _ in events:
        if not out or out[-1] != kind:
            out.append(kind)
    return out


def steps(events) -> list[str]:
    return [d["step"] for k, d in events if k == "trace"]


async def post(client, path, body):
    response = await client.post(path, json=body, headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


@pytest.fixture
def run_scenario():
    async def go(name, rounds, message, roles=("Chat.User",), client_tools=None, tool_output=None):
        app = api_for(name, ScriptedModel(*rounds), roles)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            first = await post(client, "/chat", {"message": message, "client_tools": client_tools or []})
            second = None
            if first[-1][0] == "client_tool_calls":
                paused = first[-1][1]
                second = await post(client, "/chat/tool-results", {
                    "conversation_id": paused["conversation_id"],
                    "turn_id": paused["turn_id"],
                    "results": [{"call_id": c["call_id"], "output": tool_output} for c in paused["calls"]],
                })
        return first, second

    return go


@pytest.mark.parametrize("name", BACKENDS)
async def test_plain_answer(run_scenario, name):
    first, _ = await run_scenario(
        name, [[TextDelta("Hello "), TextDelta("there"), Completed({"total_tokens": 3})]], "Hi"
    )
    assert shape(first)[0] == "trace" and shape(first)[-1] == "done" and "delta" in shape(first)
    assert "error" not in shape(first)
    assert {"token_validated", "backend_selected", "model_called"} <= set(steps(first))
    assert steps(first)[:2] == ["token_validated", "backend_selected"]
    done = first[-1][1]
    assert set(done) == {"conversation_id", "turn_id", "backend", "usage"}
    assert done["backend"] == name
    assert "".join(d["text"] for k, d in first if k == "delta") == "Hello there"


@pytest.mark.parametrize("name", BACKENDS)
async def test_client_tool_round_trip(run_scenario, name):
    rounds = [
        [FunctionCall("call_1", "make_noise", '{"times": 3}'), Completed({"total_tokens": 2})],
        [TextDelta("Beeped."), Completed({"total_tokens": 2})],
    ]
    first, second = await run_scenario(
        name, rounds, "Beep three times", client_tools=[MAKE_NOISE], tool_output="Rang 3 times"
    )
    assert first[-1][0] == "client_tool_calls" and "done" not in [k for k, _ in first]
    paused = first[-1][1]
    assert set(paused) == {"turn_id", "conversation_id", "calls"}
    assert paused["calls"] == [{"call_id": "call_1", "name": "make_noise", "arguments": '{"times": 3}'}]
    assert "client_tool_requested" in steps(first)
    assert second is not None and shape(second)[-1] == "done"
    assert second[-1][1]["turn_id"] == paused["turn_id"] and second[-1][1]["backend"] == name
    assert "".join(d["text"] for k, d in second if k == "delta") == "Beeped."


@pytest.mark.parametrize("name", BACKENDS)
async def test_authorization_denied_is_traced_and_the_turn_still_ends(run_scenario, name):
    rounds = [
        [FunctionCall("call_1", "book_test_drive", BOOK), Completed({})],
        [TextDelta("Sorry, you are not allowed to book."), Completed({})],
    ]
    first, _ = await run_scenario(name, rounds, "Book me a test drive", roles=("Chat.User",))
    order = steps(first)
    assert order.index("server_tool_called") < order.index("authorization_denied") < order.index("server_tool_returned")
    denied = next(d for k, d in first if k == "trace" and d["step"] == "authorization_denied")
    assert denied["required_role"] == "TestDrive.Book"
    assert first[-1][0] == "done"


@pytest.mark.parametrize("name", BACKENDS)
async def test_model_failure_is_an_error_event_without_detail(run_scenario, name):
    first, _ = await run_scenario(name, [RuntimeError("secret internal detail")], "Hi")
    kind, data = first[-1]
    assert kind == "error" and "done" not in [k for k, _ in first]
    assert set(data) == {"code", "message"}
    assert "secret" not in json.dumps(data)


CONTENT = "NOTE the sign says ZONE-7731"
PARTS = [
    {"type": "input_text", "text": "Attachment 1 (note): the sign"},
    {"type": "input_text", "text": CONTENT},
]
FIRST_CALLS = [("call_1", "set_speed", '{"kmh": 0}'), ("call_2", "get_sign_photo", "{}")]
SECOND_CALLS = [
    ("call_3", "set_wipers", '{"mode": "off"}'), ("call_4", "honk", '{"times": 1}'), ("call_5", "park", "{}"),
]
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENCRYPTED"}
ANSWER = "Yes, until 18."


class NoteHandler:
    """A made-up kind, text this time: nothing outside app/attachments knows what a photo is."""

    kind, max_bytes = "note", 1000

    def accepts(self, head):
        return head.startswith(b"NOTE")

    def process(self, data, label):
        return [Part(label, {"type": "input_text", "text": data.decode()}, len(data), "1 line")]


def user_text(text):
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def output(call_id):
    return {"type": "function_call_output", "call_id": call_id, "output": f"{call_id} done"}


def ids(items, kind):
    return [i["call_id"] for i in items if i["type"] == kind]


@pytest.fixture
def car_conversation(monkeypatch, caplog):
    """A turn with two pauses that uses all the car's tools, and a message after it. The attachment
    comes with the message or with the result of `get_sign_photo`."""

    async def go(name, model, place):
        monkeypatch.setattr(attachments, "HANDLERS", (*attachments.HANDLERS, NoteHandler()))
        tools = load_module("create_agents", "agents/prompt/create_agents.py").CAR_TOOLS
        auth = {"Authorization": "Bearer fake"}

        async def answer(paused, attached=()):
            turn = {k: paused[k] for k in ("conversation_id", "turn_id")}
            results = [
                {"call_id": c["call_id"], "output": f"{c['call_id']} done",
                 "attachments": list(attached) if c["name"] == "get_sign_photo" else []}
                for c in paused["calls"]
            ]
            return await post(client, "/chat/tool-results", {**turn, "results": results})

        app = api_for(name, model, ("Chat.User",))
        # The app sets up its own logging, so this comes after it.
        monkeypatch.setattr(logging.getLogger(), "handlers", [caplog.handler])
        caplog.set_level(logging.DEBUG)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            uploaded = await client.post("/attachments", files={"file": CONTENT.encode()},
                                         data={"label": "the sign"}, headers=auth)
            assert uploaded.status_code == 201, uploaded.text
            attached = [uploaded.json()["id"]]
            message = {"message": "Can I park here?", "client_tools": tools}
            if place == "message":
                message["attachments"] = attached
            first = await post(client, "/chat", message)
            assert first[-1][0] == "client_tool_calls", first
            second = await answer(first[-1][1], attached if place == "tool result" else ())
            assert second[-1][0] == "client_tool_calls", second
            third = await answer(second[-1][1])
            follow_up = {"message": "And tomorrow?", "client_tools": tools,
                         "conversation_id": first[-1][1]["conversation_id"]}
            return first, second, third, await post(client, "/chat", follow_up)

    return go


@pytest.mark.parametrize("place", ["message", "tool result"])
@pytest.mark.parametrize("name", BACKENDS)
async def test_an_attachment_over_two_pauses(car_conversation, name, place, caplog):
    model = ScriptedModel(
        [*(FunctionCall(*c) for c in FIRST_CALLS), Completed({})],
        [*(FunctionCall(*c) for c in SECOND_CALLS), Completed({}), OutputItem(REASONING)],
        [TextDelta(ANSWER), Completed({})],
        [TextDelta("Yes, all day."), Completed({})],
    )
    requests = await car_conversation(name, model, place)
    first, second, third, follow_up = requests
    in_message = place == "message"

    # What the client sees.
    paused = [r[-1][1] for r in (first, second)]
    assert [[(c["call_id"], c["name"], c["arguments"]) for c in p["calls"]] for p in paused] == [
        FIRST_CALLS, SECOND_CALLS
    ]
    assert paused[0]["turn_id"] == paused[1]["turn_id"] == third[-1][1]["turn_id"]
    assert [r[-1][0] for r in (third, follow_up)] == ["done", "done"]
    assert "".join(d["text"] for k, d in third if k == "delta") == ANSWER
    used = [[d for k, d in r if k == "trace" and d["step"] == "attachment_used"] for r in requests]
    assert [len(u) for u in used] == ([1, 0, 0, 0] if in_message else [0, 1, 0, 0])
    [trace] = sum(used, [])
    assert (trace["kind"], trace["detail"], trace["size"]) == ("note", "1 line", len(CONTENT))
    assert "ZONE-7731" not in json.dumps(requests)
    assert "turn input" in caplog.text and "ZONE-7731" not in caplog.text  # the log is at debug level

    # What the model is sent: the content in every later call of the turn, once, and none stored.
    stored = [False, False, False, True] if in_message else [True, False, False, True]
    assert [c["store"] for c in model.calls] == stored
    assert [json.dumps(c["input"]).count(CONTENT) for c in model.calls] == [int(not s) for s in stored]
    assert {c["conversation"] for c in model.calls} == {"conv_contract"}
    question = {"type": "message", "role": "user", "content": PARTS}
    if in_message:
        question["content"] = [{"type": "input_text", "text": "Can I park here?"}, *PARTS]
        sent = [question]
        assert model.calls[0]["input"] == sent
        sent += [*(function_call(*c) for c in FIRST_CALLS), output("call_1"), output("call_2")]
    else:
        assert model.calls[0]["input"] == "Can I park here?"
        # The two calls were stored, so only their outputs are sent.
        sent = [output("call_1"), output("call_2"), question]
    assert model.calls[1]["input"] == sent
    assert model.calls[2]["input"][:len(sent)] == sent
    added = model.calls[2]["input"][len(sent):]
    assert REASONING in added
    assert ids(added, "function_call") == ids(added, "function_call_output") == ["call_3", "call_4", "call_5"]
    # The message after it is stored as usual and brings nothing with it.
    assert model.calls[3]["input"] == "And tomorrow?"
    assert "conversation_repaired" not in steps(follow_up)

    # What the conversation gets: text, once.
    head = [user_text("Can I park here?\n(attachment sent)")] if in_message else [output("call_1"), output("call_2")]
    answered = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ANSWER}]}
    assert model.recorded == [("conv_contract", [*head, answered])]

    # The prompt backend keeps the car's agent over the pauses.
    assert {c["agent"] for c in model.calls} == ({"car"} if name == "prompt" else {None})

    # The hosted agent is sent the parts once, and after that what it kept itself.
    assert len(model.wire) == (4 if name == "hosted" else 0)
    if name == "hosted":
        bodies, outcomes = zip(*model.wire)
        assert [b["parts"] for b in bodies] == ([PARTS, [], [], []] if in_message else [[], PARTS, [], []])
        assert [b["kept"] for b in bodies] == [None, *(o["kept"] for o in outcomes[:3])]
        assert [CONTENT in json.dumps(o["kept"]) for o in outcomes] == [in_message, True, False, False]
        assert [b["private"] for b in bodies] == [not s for s in stored]


# A client tool the prompt agent was not defined with, so that the limit stays visible.
LEFT_OUT_OF_THE_PROMPT_AGENT = {"set_gauge"}


def test_the_cli_and_the_prompt_agent_declare_the_same_client_tools():
    """The CLI sends its tools with each request; the prompt agent has a copy in its definition
    (a prompt agent cannot take tools per request). The two copies must not drift."""
    cli = load_module("cli_tools", "clients/cli/src/voltrieve_cli/tools.py").CLIENT_TOOLS
    agent = load_module("create_agents", "agents/prompt/create_agents.py").CLI_TOOLS
    shared = [t for t in cli if t["name"] not in LEFT_OUT_OF_THE_PROMPT_AGENT]
    assert shared == agent
    from app.backends.prompt import CLI_AGENT_TOOL_NAMES, WEB_AGENT_TOOL_NAMES

    assert {t["name"] for t in shared} == CLI_AGENT_TOOL_NAMES
    assert not LEFT_OUT_OF_THE_PROMPT_AGENT & (CLI_AGENT_TOOL_NAMES | WEB_AGENT_TOOL_NAMES)


def test_the_car_tools_of_the_prompt_agent_are_the_names_the_api_expects():
    from app.backends.prompt import CAR_AGENT_TOOL_NAMES

    car = load_module("create_agents", "agents/prompt/create_agents.py").CAR_TOOLS
    assert {t["name"] for t in car} == CAR_AGENT_TOOL_NAMES == {
        "set_speed", "set_wipers", "honk", "park", "get_sign_photo"
    }
    by_name = {t["name"]: t["parameters"] for t in car}
    assert by_name["set_speed"]["required"] == ["kmh"]
    assert by_name["set_wipers"]["properties"]["mode"]["enum"] == ["off", "slow", "fast"]
    assert by_name["honk"]["properties"]["times"]["maximum"] == 5
    assert "required" not in by_name["park"] and "required" not in by_name["get_sign_photo"]


def test_the_car_and_the_prompt_agent_declare_the_same_client_tools():
    """clients/car/src/tools.ts imports this list, so it is what the car sends with each request."""
    car = json.loads((ROOT / "clients/car/src/tools.json").read_text())
    assert car == load_module("create_agents", "agents/prompt/create_agents.py").CAR_TOOLS


def test_every_caller_of_search_uses_the_same_api_version():
    """GPT-6 planning needs the newest version, and a caller left behind would meet a base it
    cannot query. Bicep and the smoke script cannot import the constant."""
    from voltrieve_search.client import DEFAULT_API_VERSION

    agents = load_module("create_agents", "agents/prompt/create_agents.py")
    assert agents.KB_API_VERSION == DEFAULT_API_VERSION == "2026-11-01-preview"
    tool = agents.kb_mcp_tool("https://s.search.windows.net/", "voltrieve-kb", "d", "c")
    assert tool.server_url == (
        f"https://s.search.windows.net/knowledgebases/voltrieve-kb/mcp?api-version={DEFAULT_API_VERSION}"
    )
    connection = (ROOT / "infra/modules/foundry-kb-connection.bicep").read_text()
    assert f"/mcp?api-version={DEFAULT_API_VERSION}'" in connection
    assert f'SEARCH_API_VERSION = "{DEFAULT_API_VERSION}"' in (ROOT / "scripts/smoke.py").read_text()


def test_the_documented_planning_model_is_the_default_in_bicep():
    """A plain provision must plan with the model the deployment page says it does."""
    import re

    row = re.search(r"^\| `PLANNING_MODEL_NAME` \| `([^`]+)` \|", (ROOT / "docs/deploy.md").read_text(), re.M)
    bicep = re.search(r"^param planningModelName string = '([^']+)'", (ROOT / "infra/main.bicep").read_text(), re.M)
    parameters = json.loads((ROOT / "infra/main.parameters.json").read_text())["parameters"]
    assert row and bicep
    assert row[1] == bicep[1]
    assert parameters["planningModelName"]["value"] == f"${{PLANNING_MODEL_NAME={bicep[1]}}}"
