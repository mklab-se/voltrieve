"""Who may order a car, through /chat with signed tokens, the real backends and a fake dealer."""

import json

import httpx
import pytest
from voltrieve_agent.dealer import DealerClient
from voltrieve_agent.model import Completed, FunctionCall, TextDelta
from voltrieve_agent.server_tools import CAR_REFUSAL, KB_SEARCH_TOOLS, OTHER_REFUSAL, build_server_tools

from app.backends import BackendRegistry
from app.backends.loop import LoopBackend
from app.backends.prompt import PromptBackend
from app.main import create_app
from app.settings import Settings
from app.turns import TurnStore
from tests.conftest import parse_sse
from tests.test_loop_backend import FakeOpenAI, ScriptedModel

AZURE_CLI = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
MAKE_NOISE = {"name": "make_noise", "description": "Ring", "parameters": {"type": "object"}}
SET_SPEED = {"name": "set_speed", "description": "Speed", "parameters": {"type": "object"}}
ADDRESS = "Storgatan 1, 753 20 Uppsala"
OFFER = FunctionCall("c1", "prepare_car_order", json.dumps({"model": "fjord", "address": ADDRESS}))
PLACE = FunctionCall("c1", "place_car_order", '{"offer": "offer-1"}')
NOISE = FunctionCall("n1", "make_noise", "{}")
ANSWER = [TextDelta("ok"), Completed({})]


class Shop:
    """The API with one real backend, its model's script and the requests the dealer got."""

    def __init__(self, name, validator, make_token):
        self.dealer_got, self._make_token = [], make_token

        async def dealer(request):
            self.dealer_got.append(json.loads(request.content))
            return httpx.Response(201, json={"offer": "offer-1"})

        async def token():
            return "tok"

        client = DealerClient("https://dealer.test", token, httpx.AsyncClient(transport=httpx.MockTransport(dealer)))
        tools = build_server_tools(client, None)
        self.model = ScriptedModel()
        if name == "loop":
            backend = LoopBackend(self.model, FakeOpenAI(), tools)
        else:
            tools = [t for t in tools if t.name not in KB_SEARCH_TOOLS]
            backend = PromptBackend(self.model, self.model, self.model, FakeOpenAI(), tools)
        settings = Settings(agent_backend=name, car_client_id="car-app", web_client_id="web-app",
                            cli_client_id="cli-app")
        app = create_app(settings, validator, BackendRegistry(settings, {name: lambda s: backend}), TurnStore())
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def post(self, path, azp, body, headers):
        token = self._make_token(azp=azp, name="Anna A", roles=["Chat.User", "Order.Place"])
        r = await self.http.post(path, json=body, headers={"Authorization": f"Bearer {token}", **headers})
        assert r.status_code == 200, r.text
        return parse_sse(r.text)

    async def say(self, azp, *rounds, headers=(), **body):
        self.model.rounds = list(rounds)
        return await self.post("/chat", azp, {"message": "Order a Fjord", **body}, dict(headers))

    async def go_on(self, azp, paused, headers=()):
        body = {
            "conversation_id": paused["conversation_id"], "turn_id": paused["turn_id"],
            "results": [{"call_id": c["call_id"], "output": "done"} for c in paused["calls"]],
        }
        return await self.post("/chat/tool-results", azp, body, dict(headers))

    def refused(self, events, text=CAR_REFUSAL):
        steps = [d for k, d in events if k == "trace" and d["step"] == "client_not_allowed"]
        assert events[-1][0] == "done" and ADDRESS not in json.dumps(events)
        told = [i["output"] for i in self.model.inputs[-1] if i["call_id"] == "c1"]
        return len(steps) == 1 and self.dealer_got == [] and told == [text]


@pytest.fixture(params=["loop", "prompt"])
async def shop(request, validator, make_token):
    shop = Shop(request.param, validator, make_token)
    yield shop
    await shop.http.aclose()


@pytest.mark.parametrize("call", [OFFER, PLACE])
@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, {}),
        ({}, {"client_tools": [SET_SPEED]}),
        ({}, {"client_tools": [MAKE_NOISE]}),
        ({"X-Voltrieve-Client": "web"}, {}),
        ({"X-Voltrieve-Client": "car"}, {}),
        ({"X-Voltrieve-Test": "1"}, {}),
        ({"X-Voltrieve-Test": "1", "X-Voltrieve-Client": "cli"}, {}),
    ],
)
async def test_the_car_cannot_order(shop, call, headers, body):
    assert shop.refused(await shop.say("car-app", [call], ANSWER, headers=headers, **body))


@pytest.mark.parametrize("azp", ["web-app", "cli-app", AZURE_CLI, "someone-else"])
async def test_the_header_makes_every_client_the_car(shop, azp):
    assert shop.refused(await shop.say(azp, [OFFER], ANSWER, headers={"X-Voltrieve-Client": "car"}))


@pytest.mark.parametrize("azp", ["someone-else", ""])
async def test_an_unknown_client_cannot_order(shop, azp):
    assert shop.refused(await shop.say(azp, [OFFER], ANSWER, headers={"X-Voltrieve-Client": "web"}), OTHER_REFUSAL)


@pytest.mark.parametrize(
    ("azp", "client", "headers"),
    [
        ("web-app", "web", {}), ("cli-app", "cli", {}), (AZURE_CLI, "cli", {}),
        ("web-app", "web", {"X-Voltrieve-Test": "1"}), ("web-app", "web", {"X-Voltrieve-Client": "cli"}),
    ],
)
async def test_the_website_and_the_cli_can_order(shop, azp, client, headers):
    events = await shop.say(azp, [OFFER], ANSWER, headers=headers)
    turn = events[-1][1]["turn_id"]
    assert shop.dealer_got == [{
        "model": "fjord", "address": ADDRESS, "customer": "oid-anna", "name": "Anna A", "turn": turn,
        "client": client,
    }]
    assert ADDRESS not in json.dumps(events)


async def test_the_next_message_is_another_turn(shop):
    first = (await shop.say("web-app", [OFFER], ANSWER))[-1][1]
    await shop.say("web-app", [PLACE], ANSWER, conversation_id=first["conversation_id"])
    assert shop.dealer_got[0]["turn"] != shop.dealer_got[1]["turn"]


async def test_the_car_cannot_order_in_a_conversation_of_the_website(shop):
    first = (await shop.say("web-app", ANSWER))[-1][1]
    events = await shop.say("car-app", [OFFER], ANSWER, conversation_id=first["conversation_id"])
    assert shop.refused(events)


CAR_HEADER = {"X-Voltrieve-Client": "car"}


@pytest.mark.parametrize(
    "requests",
    [
        [("web-app", {}), ("car-app", {})],
        [("car-app", {}), ("web-app", {})],
        [("web-app", {}), ("web-app", CAR_HEADER)],
        [("cli-app", CAR_HEADER), ("cli-app", {})],
        [("web-app", {}), ("car-app", {}), ("web-app", {})],
        [("car-app", {}), ("web-app", {}), ("web-app", {})],
        [("web-app", {}), ("someone-else", {})],
    ],
)
async def test_a_paused_turn_that_the_car_had_a_part_in_cannot_order(shop, requests):
    rounds = [*([NOISE] for _ in requests[1:]), [OFFER], ANSWER]
    (azp, headers), *later = requests
    events = await shop.say(azp, *rounds, headers=headers, client_tools=[MAKE_NOISE])
    for azp, headers in later:
        events = await shop.go_on(azp, events[-1][1], headers)
    assert shop.refused(events, OTHER_REFUSAL if azp == "someone-else" else CAR_REFUSAL)


async def test_a_paused_turn_goes_on_as_the_client_that_continues_it_in_the_same_turn(shop):
    events = await shop.say("cli-app", [NOISE], [OFFER], ANSWER, client_tools=[MAKE_NOISE])
    paused = events[-1][1]
    await shop.go_on("web-app", paused)
    assert [(g["client"], g["turn"]) for g in shop.dealer_got] == [("web", paused["turn_id"])]
