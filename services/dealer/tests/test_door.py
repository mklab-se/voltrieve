"""The MCP door of the dealers, through a real client of both generations."""

import asyncio
import json
import logging
import unicodedata
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import Client
from mcp import types as t
from mcp.client.streamable_http import streamable_http_client
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_orders import ADDRESS, NAME, UNAVAILABLE, BrokenStore, log
from test_staff import ROUTES, an_order, client_of
from voltrieve_doors import DoorTokenValidator

from app import door
from app.main import create_app
from app.model import StoreUnavailable
from app.settings import Settings
from app.store import MemoryStore
from app.telemetry import instrument_app

HOST = "dealer.example.test"
PATH = "/mcp/dealer"
DOOR_ID = "00000000-0000-0000-0000-0000000000dd"
CLAUDE = "22222222-2222-2222-2222-222222222222"
CITIES = "oid-malmo=Malmö; oid-uppsala=Uppsala;oid-robot=Malmö;"
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
EVERY_CALL = [
    ("my_stock", {}), ("change_stock", {"model": "fjord", "change": 1}), ("open_orders", {}),
    ("deliver_order", {"number": "VO-1001"}),
]  # fmt: skip
BOTH = pytest.mark.parametrize("mode", ["auto", "legacy"])


def door_settings(**changes) -> Settings:
    values = {"mcp_dealer_url": f"https://{HOST}", "mcp_dealer_client_id": DOOR_ID, "mcp_apps": f"{CLAUDE}=Claude"}
    return Settings(**{**values, "dealer_door_cities": CITIES, **changes})


class World:
    def __init__(self, make_token, validators, jwks, store=None):
        self.make_token = make_token
        settings = door_settings()
        check = DoorTokenValidator(
            settings.tenant_id, DOOR_ID, f"api://{DOOR_ID}", frozenset({"dealer.use"}), {CLAUDE: "Claude"},
            applications=True, jwks_fetcher=lambda: jwks,
        )  # fmt: skip
        self.store = store or MemoryStore()
        self.app = create_app(
            settings, validators[0], staff_validator=validators[1], door_validator=check, store=self.store
        )

    def person(self, oid="oid-malmo", roles=("Dealer.Staff",), **claims) -> str:
        claims = {"oid": oid, "scp": "dealer.use", "name": "Bo Exempel", **claims}
        return self.make_token(aud=DOOR_ID, roles=list(roles), claims=claims)

    def robot(self, oid="oid-robot", roles=("Dealer.Agent",), **claims) -> str:
        return self.make_token(aud=DOOR_ID, roles=list(roles), claims={"oid": oid, "azp": "a-robot", **claims})

    def http(self, token, **headers):
        transport = httpx2.ASGITransport(app=self.app)
        bearer = {"authorization": f"Bearer {token}"}
        return httpx2.AsyncClient(transport=transport, base_url=f"https://{HOST}", headers={**bearer, **headers})

    @asynccontextmanager
    async def client(self, token, mode="auto", elicit=None):
        async with self.http(token) as http:
            address = streamable_http_client(f"https://{HOST}{PATH}", http_client=http)
            async with Client(address, mode=mode, elicitation_callback=elicit) as client:
                yield client

    async def call(self, token, tool, arguments=None, mode="auto", elicit=None) -> "Answer":
        async with self.client(token, mode, elicit) as client:
            return Answer(await client.call_tool(tool, arguments or {}))

    async def order(self, **changes) -> str:
        async with client_of(self.app, self.make_token()) as runa:
            number = await an_order(runa, **changes)
        self.app.state.hub.clear()
        return number

    async def in_stock(self, model, city) -> int:
        return (await self.store.stock())[model][city]

    async def status(self, number) -> str:
        return next(order.status for order in await self.store.orders() if order.number == number)

    def said(self) -> list[tuple]:
        fields = ("kind", "who", "client", "model", "city", "number", "result")
        return [tuple(entry[field] for field in fields) for entry in log(self.app)]


class Answer:
    def __init__(self, result):
        self.refused = result.is_error
        self.text = result.content[0].text

    @property
    def json(self):
        assert not self.refused, self.text
        return json.loads(self.text)

    def refuses(self, text) -> bool:
        return self.refused and self.text.endswith(text)


class Person:
    """Answers the question of the server, after something happened."""

    def __init__(self, action="accept", deliver=True, meanwhile=None):
        self.action, self.deliver, self.meanwhile = action, deliver, meanwhile
        self.questions = []

    async def __call__(self, ctx, params):
        self.questions.append(params.message)
        if self.meanwhile:
            await self.meanwhile()
        content = {"deliver": self.deliver} if self.action == "accept" else None
        return t.ElicitResult(action=self.action, content=content)


@asynccontextmanager
async def open_world(*args, **options):
    """Started and stopped in one task, as a lifespan is."""
    world = World(*args, **options)
    started, stop = asyncio.Event(), asyncio.Event()

    async def life():
        async with world.app.router.lifespan_context(world.app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(life())
    await started.wait()
    try:
        yield world
    finally:
        stop.set()
        await task


@pytest.fixture
def parts(make_token, validator, staff_validator, jwks):
    return make_token, (validator, staff_validator), jwks


@pytest.fixture
async def world(parts):
    async with open_world(*parts) as world:
        yield world


def rpc(tool, arguments):
    return {"jsonrpc": "2.0", "id": "1", "method": "tools/call", "params": {"name": tool, "arguments": arguments}}


# ---- what each may see and do


@BOTH
async def test_what_a_person_and_an_application_see(world, mode):
    async with world.client(world.person(), mode) as c:
        tools = {tool.name: tool for tool in (await c.list_tools()).tools}
    assert list(tools) == ["my_stock", "change_stock", "open_orders", "deliver_order"]
    assert [name for name, tool in tools.items() if tool.annotations.read_only_hint] == ["my_stock", "open_orders"]
    assert [name for name, tool in tools.items() if tool.annotations.destructive_hint] == ["deliver_order"]
    assert tools["change_stock"].input_schema["properties"]["model"]["enum"][0] == "spark"
    assert {name for tool in tools.values() for name in tool.input_schema["properties"]} == {
        "model", "change", "after", "number",
    }  # fmt: skip
    async with world.client(world.robot(), mode) as c:
        assert [tool.name for tool in (await c.list_tools()).tools] == ["my_stock", "change_stock", "open_orders"]


@BOTH
async def test_the_stock_of_two_cities(world, mode):
    here = (await world.call(world.person(), "my_stock", mode=mode)).json
    there = (await world.call(world.person("oid-uppsala"), "my_stock", mode=mode)).json
    assert here["city"] == "Malmö" and there["city"] == "Uppsala"
    assert here["cars"][:2] == [{"model": "spark", "in_stock": 5}, {"model": "lumen", "in_stock": 2}]
    assert there["cars"][:2] == [{"model": "spark", "in_stock": 0}, {"model": "lumen", "in_stock": 4}]
    assert world.said()[0] == ("stock", "Bo Exempel", "Claude", None, "Malmö", None, "11")


@BOTH
async def test_a_change_of_the_stock(world, mode):
    seen = world.app.state.hub.subscribe()
    up = await world.call(world.person(), "change_stock", {"model": "fjord", "change": 1}, mode)
    assert up.json == {"model": "fjord", "city": "Malmö", "in_stock": 4}
    down = await world.call(world.robot(), "change_stock", {"model": "fjord", "change": -1}, mode)
    assert down.json["in_stock"] == 3
    assert world.said() == [
        ("stock_changed", "Bo Exempel", "Claude", "fjord", "Malmö", None, "4"),
        ("stock_changed", "Dealer agent", "application", "fjord", "Malmö", None, "3"),
    ]
    assert [seen.get_nowait()[0] for _ in range(4)] == ["log", "changed"] * 2


async def test_a_person_without_a_name(world):
    await world.call(world.person(name=None), "my_stock")
    assert world.said()[0][1] == "Representative"


@pytest.mark.parametrize("change", [True, 1.0, "1", 2, 0, None, [1], -1.0, False])
async def test_a_change_that_is_not_one_car(world, change):
    body = rpc("change_stock", {"model": "fjord", "change": change})
    async with world.http(world.person(), **HEADERS) as c:
        answer = (await c.post(PATH, json=body, headers={"mcp-protocol-version": "2025-11-25"})).json()
    assert answer["result"]["isError"] and await world.in_stock("fjord", "Malmö") == 3


async def test_a_change_that_cannot_be_made(world):
    for _ in range(3):
        await world.call(world.person(), "change_stock", {"model": "fjord", "change": -1})
    empty = await world.call(world.person(), "change_stock", {"model": "fjord", "change": -1})
    assert empty.refuses(": There is none in stock in Malmö.")
    unknown = await world.call(world.person(), "change_stock", {"model": "MARKER", "change": 1})
    assert unknown.refuses(": Unknown model.")
    assert world.said()[-2:] == [
        ("change_asked", "Bo Exempel", "Claude", "fjord", "Malmö", None, "There is none in stock in Malmö."),
        ("change_asked", "Bo Exempel", "Claude", None, "Malmö", None, "Unknown model."),
    ]


async def test_changes_at_the_same_time_never_go_below_zero(world):
    async def one(token):
        return await world.call(token, "change_stock", {"model": "spark", "change": -1})

    answers = []
    for _ in range(3):  # four at once, as many as the door takes
        answers += await asyncio.gather(*(one(token) for token in [world.person(), world.robot()] * 2))
    assert sorted(a.json["in_stock"] for a in answers if not a.refused) == [0, 1, 2, 3, 4]
    assert [a.refuses("There is none in stock in Malmö.") for a in answers if a.refused] == [True] * 7
    assert await world.in_stock("spark", "Malmö") == 0


@BOTH
@pytest.mark.parametrize("forged", [{"city": "Uppsala"}, {"who": "x", "oid": "oid-uppsala", "app": "y"}])
async def test_forged_arguments_change_nothing(world, mode, forged):
    number = await world.order()
    here = world.person()
    assert (await world.call(here, "my_stock", forged, mode)).json["city"] == "Malmö"
    changed = await world.call(here, "change_stock", {"model": "fjord", "change": 1, **forged}, mode)
    assert changed.json["city"] == "Malmö" and await world.in_stock("fjord", "Uppsala") == 2
    assert (await world.call(here, "open_orders", forged, mode)).json["city"] == "Malmö"
    yes = {"answer": {"action": "accept", "data": {"deliver": True}}}
    assert (await world.call(here, "deliver_order", {"number": number, **yes, **forged}, mode)).refused
    assert await world.status(number) == "open"


# ---- the orders


@BOTH
async def test_the_open_orders(world, mode):
    first = await world.order()  # a fjord
    second = await world.order(model="ragn")  # none in Malmö
    third = await world.order(model="spark")
    await world.store.deliver(third, "Malmö", "x", world.app.state.clock())
    answer = (await world.call(world.robot(), "open_orders", mode=mode)).json
    assert answer["city"] == "Malmö" and answer["more"] is False
    assert [(o["number"], o["model"], o["price"], o["in_own_stock"]) for o in answer["orders"]] == [
        (first, "fjord", 529000, True), (second, "ragn", 549000, False),
    ]  # fmt: skip
    assert set(answer["orders"][0]) == {"number", "model", "price", "currency", "ordered", "in_own_stock"}
    later = (await world.call(world.person(), "open_orders", {"after": first}, mode)).json
    assert [o["number"] for o in later["orders"]] == [second]
    assert world.said() == [
        ("open_orders", "Dealer agent", "application", None, "Malmö", None, "2"),
        ("open_orders", "Bo Exempel", "Claude", None, "Malmö", None, "1"),
    ]


async def test_at_most_50_orders_at_once_and_none_is_lost(world):
    numbers = [await world.order() for _ in range(53)]
    first = (await world.call(world.person(), "open_orders")).json
    assert [o["number"] for o in first["orders"]] == numbers[:50] and first["more"] is True
    rest = (await world.call(world.person(), "open_orders", {"after": numbers[49]})).json
    assert [o["number"] for o in rest["orders"]] == numbers[50:] and rest["more"] is False


@pytest.mark.parametrize("after", ["1001", "VO-", "VO-1001 ", "VO-1001\n", "vo-1001", "VO-١٢", "VO-1234567890", 1001])
async def test_after_is_the_number_of_an_order(world, after):
    assert (await world.call(world.person(), "open_orders", {"after": after})).refused
    assert (await world.call(world.person(), "deliver_order", {"number": after}, elicit=Person())).refused
    assert log(world.app) == []


# ---- a delivery


async def test_a_delivery_after_a_yes(world):
    number, person = await world.order(), Person()
    seen = world.app.state.hub.subscribe()
    answer = await world.call(world.person(), "deliver_order", {"number": number}, elicit=person)
    assert answer.json == {"number": number, "model": "fjord", "city": "Malmö", "status": "delivered"}
    assert person.questions == [f"Deliver order {number}, Fjord, from the stock in Malmö?"]
    assert await world.in_stock("fjord", "Malmö") == 2 and await world.status(number) == "delivered"
    assert (await world.store.orders())[0].delivered_by == "Bo Exempel"
    assert world.said() == [("delivered", "Bo Exempel", "Claude", "fjord", "Malmö", number, None)]
    assert [seen.get_nowait()[0] for _ in range(2)] == ["log", "changed"]


@pytest.mark.parametrize("person", [Person("decline"), Person("cancel"), Person(deliver=False)])
async def test_no_yes_delivers_nothing(world, person):
    number = await world.order()
    answer = await world.call(world.person(), "deliver_order", {"number": number}, elicit=person)
    assert answer.refuses("The person did not say yes. Nothing was delivered.") and len(person.questions) == 1
    assert await world.status(number) == "open" and await world.in_stock("fjord", "Malmö") == 3
    assert world.said() == [
        ("deliver_asked", "Bo Exempel", "Claude", None, "Malmö", number, "No yes from the person")
    ]


@pytest.mark.parametrize("deliver", ["MARKER", "yes", "true", 1])
async def test_a_yes_that_is_not_a_boolean_delivers_nothing(world, deliver):
    number = await world.order()
    answer = await world.call(world.person(), "deliver_order", {"number": number}, elicit=Person(deliver=deliver))
    assert answer.refused and await world.status(number) == "open"


@pytest.mark.parametrize("mode, elicit", [("auto", None), ("legacy", Person()), ("legacy", None)])
async def test_a_client_that_cannot_ask_delivers_nothing(world, mode, elicit):
    number = await world.order()
    answer = await world.call(world.person(), "deliver_order", {"number": number}, mode, elicit)
    assert answer.refuses(door.CANNOT_ASK) and await world.status(number) == "open"
    assert not elicit or not elicit.questions
    assert [(e[0], e[3], e[5], e[6]) for e in world.said()] == [
        ("deliver_asked", "fjord", number, "The app cannot ask"),
    ]


async def test_what_cannot_be_delivered_is_not_asked_about(world):
    person, none_here = Person(), await world.order(model="ragn")
    delivered = await world.order()
    await world.store.deliver(delivered, "Uppsala", "x", world.app.state.clock())

    async def deliver(number):
        return await world.call(world.person(), "deliver_order", {"number": number}, elicit=person)

    assert (await deliver(none_here)).refuses(": There is none in stock in Malmö.")
    assert (await deliver(delivered)).refuses(": This order is already delivered.")
    assert (await deliver("VO-9999")).refuses(": Unknown order.")
    assert person.questions == [] and await world.in_stock("fjord", "Malmö") == 3
    assert [entry[3:] for entry in world.said()] == [
        ("ragn", "Malmö", none_here, "There is none in stock in Malmö."),
        ("fjord", "Malmö", delivered, "This order is already delivered."),
        (None, "Malmö", None, "Unknown order."),
    ]


async def test_what_changed_between_the_question_and_the_yes(world):
    async def deliver(number, meanwhile):
        person = Person(meanwhile=meanwhile)
        answer = await world.call(world.person(), "deliver_order", {"number": number}, elicit=person)
        assert len(person.questions) == 1
        return answer

    one, two, three = [await world.order() for _ in range(3)]

    async def someone_delivers():
        await world.store.deliver(one, "Uppsala", "x", world.app.state.clock())

    async def someone_deletes():
        await world.store.delete(two)

    async def the_stock_goes():
        for _ in range(3):
            await world.store.change_stock("fjord", "Malmö", -1)

    assert (await deliver(one, someone_delivers)).refuses(": This order is already delivered.")
    assert (await deliver(two, someone_deletes)).refuses(": Unknown order.")
    assert (await deliver(three, the_stock_goes)).refuses(": There is none in stock in Malmö.")
    assert await world.status(three) == "open" and await world.in_stock("fjord", "Malmö") == 0


async def test_the_delivery_itself_checks_too(parts):
    """The last moment: after the second look, before the write."""

    class Racing(MemoryStore):
        async def deliver(self, *args):
            await super().change_stock("fjord", "Malmö", -1)
            return await super().deliver(*args)

    async with open_world(*parts, store=Racing()) as world:
        number = await world.order()
        for _ in range(2):
            await world.store.change_stock("fjord", "Malmö", -1)
        answer = await world.call(world.person(), "deliver_order", {"number": number}, elicit=Person())
        assert answer.refuses("There is none in stock in Malmö.") and await world.status(number) == "open"


async def test_one_order_is_delivered_once(world):
    number = await world.order()
    tokens = [world.person(), world.person("oid-uppsala")]
    answers = await asyncio.gather(
        *(world.call(token, "deliver_order", {"number": number}, elicit=Person()) for token in tokens)
    )
    assert sorted(answer.refused for answer in answers) == [False, True]
    assert await world.in_stock("fjord", "Malmö") + await world.in_stock("fjord", "Uppsala") == 4


# ---- who may not


@BOTH
@pytest.mark.parametrize("tool, arguments", EVERY_CALL)
async def test_a_caller_without_a_city(world, mode, tool, arguments):
    await world.order()
    person = Person()
    answer = await world.call(world.person("oid-other"), tool, arguments, mode, person)
    assert answer.refuses(door.NO_CITY) and person.questions == []
    assert world.said() == [("refused", "Bo Exempel", "Claude", None, None, None, None)]


@BOTH
@pytest.mark.parametrize("tool, arguments", EVERY_CALL)
@pytest.mark.parametrize("who", ["person", "robot"])
async def test_a_role_of_the_other_kind_opens_nothing(world, mode, tool, arguments, who):
    number = await world.order()
    token = world.robot(roles=["Dealer.Staff"])
    if who == "person":
        token = world.person(roles=["Dealer.Agent", "Dealer.Manage"])
    person = Person()
    async with world.client(token, mode, person) as c:
        assert (await c.list_tools()).tools == []
        answer = Answer(await c.call_tool(tool, arguments))
    assert answer.refused and answer.text == "You may not use this tool."
    assert person.questions == [] and await world.status(number) == "open"
    app = "Claude" if who == "person" else "application"
    assert world.said() == [("refused", None, app, None, None, None, None)]


@BOTH
async def test_an_application_never_delivers(world, mode):
    number = await world.order()
    for token in (world.robot(), world.robot(roles=["Dealer.Agent", "Dealer.Staff"])):
        answer = await world.call(token, "deliver_order", {"number": number}, mode, Person())
        assert answer.refused and answer.text == "You may not use this tool."
    assert await world.status(number) == "open"


@pytest.mark.parametrize("tool", ["delete_order", "reset", "delete"])
async def test_there_is_no_delete_and_no_reset(world, tool):
    number = await world.order()
    async with world.http(world.person(), **HEADERS) as c:
        answer = (await c.post(PATH, json=rpc(tool, {"number": number}))).json()
    assert "error" in answer or answer["result"]["isError"]
    assert await world.status(number) == "open"


@pytest.mark.parametrize("claims", [{"scp": "manage_as_user"}, {"scp": ""}, {"azp": "unknown"}, {"azp": None}])
async def test_a_token_without_the_rights(world, claims):
    async with world.http(world.person(**claims), **HEADERS) as c:
        assert (await c.post(PATH, json=rpc("my_stock", {}))).status_code == 403


async def test_the_tokens_of_the_other_routes_do_not_open_the_door(world):
    tokens = [
        world.make_token(),
        world.make_token(roles=["Dealer.Manage"], claims={"scp": "manage_as_user", "name": "Bo Exempel"}),
        world.make_token(roles=["Dealer.Staff"], claims={"scp": "dealer.use", "oid": "oid-malmo"}),
        world.make_token(roles=["Dealer.Agent"], claims={"oid": "oid-robot"}),
        "not-a-jwt",
    ]
    for token in tokens:
        async with world.http(token, **HEADERS) as c:
            answer = await c.post(PATH, json=rpc("my_stock", {}))
        assert answer.status_code == 401 and f"{HOST}/.well-known/" in answer.headers["www-authenticate"]


async def test_a_token_of_the_door_opens_no_other_route(world):
    others = [("GET", "/stock"), ("GET", "/prices"), ("POST", "/orders"), ("POST", "/test-drives")]
    roles = ["Dealer.Staff", "Dealer.Agent", "Dealer.Manage", "Dealer.Access"]
    tokens = [world.person(roles=roles, scp="dealer.use manage_as_user"), world.robot(roles=roles)]
    for token in tokens:
        async with client_of(world.app, token) as c:
            for method, path in ROUTES + others:
                assert (await c.request(method, path)).status_code == 401, path


async def test_a_signed_in_browser_does_not_call_the_door(world):
    async with world.http(world.person(), origin=f"https://{HOST}", **HEADERS) as c:
        assert (await c.post(PATH, json=rpc("my_stock", {}))).status_code == 403
        assert (await c.get(PATH)).status_code == 403
    async with world.http(world.person(), **HEADERS) as c:
        assert (await c.post(PATH, content=b"x" * 70_000)).status_code == 413


# ---- the store


@BOTH
@pytest.mark.parametrize("tool, arguments", EVERY_CALL)
async def test_a_store_that_gives_no_answer(parts, mode, tool, arguments):
    class Broken(BrokenStore):
        async def close(self):
            pass

    async with open_world(*parts, store=Broken()) as world:
        answer = await world.call(world.person(), tool, arguments, mode, Person())
    assert answer.refuses(UNAVAILABLE)


@pytest.mark.parametrize("failing", ["change_stock", "deliver"])
async def test_a_write_that_failed_makes_the_pages_look_again(parts, failing):
    class Failing(MemoryStore):
        pass

    async def fails(*_):
        raise StoreUnavailable("the store gave no answer")

    setattr(Failing, failing, fails)
    async with open_world(*parts, store=Failing()) as world:
        number = await world.order()
        seen = world.app.state.hub.subscribe()
        call = ("change_stock", {"model": "fjord", "change": 1}) if failing == "change_stock" else (
            "deliver_order", {"number": number},
        )  # fmt: skip
        assert (await world.call(world.person(), *call, elicit=Person())).refuses(UNAVAILABLE)
        assert [seen.get_nowait()[0] for _ in range(2)] == ["changed", "log"]
        assert world.said()[-1][-1] == UNAVAILABLE


async def test_an_error_that_nobody_expected_says_nothing(parts, caplog):
    class Odd(MemoryStore):
        async def orders(self):
            raise RuntimeError(f"{NAME} {ADDRESS}")

    caplog.set_level(logging.DEBUG)
    async with open_world(*parts, store=Odd()) as world:
        logging.getLogger().addHandler(caplog.handler)  # the service just replaced the handlers
        answers = [await world.call(world.person(), *call, elicit=Person()) for call in EVERY_CALL[2:]]
    assert caplog.records
    assert all(answer.refused and "Mark" not in answer.text for answer in answers)
    assert "Mark" not in everything(caplog)


# ---- what is kept


def everything(caplog) -> str:
    return "\n".join(f"{r.__dict__}{r.getMessage()}" for r in caplog.records if not r.name.startswith("asyncio"))


@BOTH
async def test_no_name_and_no_address_in_an_answer_a_log_or_a_span(world, mode, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    exporter, provider = InMemorySpanExporter(), TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("voltrieve_doors.door.tracer", provider.get_tracer("doors"))
    number = await world.order()
    token = world.person(name="Staff MARKERNAME", preferred_username="MARKERUSER")
    texts, person = [], Person()
    caplog.clear()
    instrument_app(world.app, tracer_provider=provider)
    try:
        async with world.client(token, mode, person) as c:
            for _ in range(2):  # the second delivery is refused
                for tool, arguments in EVERY_CALL:
                    arguments = {**arguments, "MARKERKEY": "MARKERARG"}
                    texts.append(Answer(await c.call_tool(tool, arguments)).text)
            for arguments in ({"number": "MARKERARG"}, {"number": number, "model": "MARKERARG"}):
                await c.call_tool("deliver_order", arguments)
            await c.call_tool("change_stock", {"model": "MARKERARG", "change": 1})
    finally:
        FastAPIInstrumentor.uninstrument_app(world.app)
    spans = exporter.get_finished_spans()
    calls = [span for span in spans if span.name == "door.call"]
    assert calls and all(span.attributes["voltrieve.door.door"] == "dealer" for span in calls)
    kept = "".join(span.to_json() for span in spans) + everything(caplog)
    assert "Mark" not in "".join(texts + person.questions) and "MARKER" not in "".join(texts + person.questions)
    assert "Mark" not in kept and "MARKER" not in kept and "oid-malmo" not in kept
    assert number not in kept
    assert "MARKERARG" not in str(log(world.app)) and "Mark" not in str(log(world.app))
    assert (await world.status(number) == "delivered") == (mode == "auto")


async def test_a_call_has_its_correlation_id_and_its_line_in_the_access_log(world, caplog):
    caplog.set_level(logging.INFO)
    body = rpc("my_stock", {})
    extra = {"mcp-protocol-version": "2025-11-25", "x-correlation-id": "cid-5"}
    async with world.http(world.person(), **HEADERS, **extra) as c:
        answer = await c.post(PATH, json=body)
    assert answer.headers["x-correlation-id"] == "cid-5" and answer.headers["cache-control"] == "no-store"
    assert log(world.app)[0]["correlation_id"] == "cid-5"
    assert [r.path for r in caplog.records if r.getMessage() == "request"] == [PATH]
    call = next(r for r in caplog.records if r.getMessage() == "door_call")
    assert (call.door, call.tool, call.app, call.result) == ("dealer", "my_stock", "Claude", "done")


# ---- the settings


@pytest.mark.parametrize("missing", ["mcp_dealer_url", "mcp_dealer_client_id"])
async def test_without_the_settings_there_is_no_door(parts, missing):
    app = create_app(door_settings(**{missing: ""}), parts[1][0], staff_validator=parts[1][1])
    async with app.router.lifespan_context(app), client_of(app, "x") as c:
        for path in (PATH, f"/.well-known/oauth-protected-resource{PATH}"):
            assert (await c.get(path)).status_code == 404
        assert (await c.post(PATH, json=rpc("my_stock", {}))).status_code == 404


@pytest.mark.parametrize(
    "wrong, left_out",
    [("oid=Atlantis", 1), ("oid", 1), ("oid=", 1), ("=Malmö", 1), ("oid=Malmö;oid=Uppsala", 1), ("oid=malmö", 1),
     ("oid=Malmö;oid=Uppsala;oid=Malmö", 2), ("oid=Atlantis;oid=Malmö", 2)],
)  # fmt: skip
async def test_a_wrong_entry_is_left_out_and_the_service_starts(capsys, wrong, left_out):
    app = create_app(door_settings(dealer_door_cities=f"{wrong};other=Uppsala"))
    lines = [line for line in capsys.readouterr().out.splitlines() if "DEALER_DOOR_CITIES" in line]
    assert len(lines) == left_out
    assert door.door_cities(f"{wrong};other=Uppsala", app.state.data) == {"other": "Uppsala"}
    assert "oid=" not in "".join(lines) and "Atlantis" not in "".join(lines)


@pytest.mark.parametrize("form", ["NFC", "NFD"])
async def test_a_city_is_known_however_its_letters_are_written(parts, form):
    written = unicodedata.normalize(form, "oid=Göteborg")
    assert door.door_cities(written, create_app(Settings()).state.data) == {"oid": "Göteborg"}


async def test_an_empty_list_of_cities_is_a_door_for_nobody(parts):
    assert door.door_cities(" ; ", create_app(Settings()).state.data) == {}


@pytest.mark.parametrize("is_url, identifier", [(False, f"api://{DOOR_ID}"), (True, f"https://{HOST}{PATH}")])
async def test_what_the_door_says_of_its_sign_in(is_url, identifier):
    settings = door_settings(mcp_identifier_is_url=is_url, mcp_dealer_url=f"https://{HOST}/")
    app = create_app(settings)
    async with client_of(app) as c:
        answer = await c.get(f"https://{HOST}/.well-known/oauth-protected-resource{PATH}")
    assert answer.json()["resource"] == f"https://{HOST}{PATH}"
    assert answer.json()["scopes_supported"] == [f"{identifier}/dealer.use"]


def test_the_apps_that_may_call():
    assert door.app_names(door_settings(mcp_apps=" a = One ;b=;=c; ")) == {"a": "One"}
    with_cli = door.app_names(door_settings(mcp_allow_cli=True))
    assert with_cli == {CLAUDE: "Claude", door.AZURE_CLI_CLIENT_ID: "Azure CLI"}


async def test_the_limit_of_calls(world):
    async with world.http(world.person(), **HEADERS) as c:
        codes = [(await c.post(PATH, json=rpc("my_stock", {}))).status_code for _ in range(31)]
    assert codes[-1] == 429 and 429 not in codes[:30]
