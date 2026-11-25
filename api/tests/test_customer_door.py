"""The customer's door with signed tokens, both generations of clients and the real tools and turns."""

import asyncio
import json
import logging

import httpx
import httpx2
import pytest
import voltrieve_doors.door as door_module
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from voltrieve_agent.curated import CURATED_ANSWERS
from voltrieve_agent.dealer import DealerClient
from voltrieve_agent.events import AgentEvent, trace
from voltrieve_agent.knowledge import QUOTA_MESSAGE
from voltrieve_agent.loop import TurnOutcome
from voltrieve_agent.model import Completed, FunctionCall, TextDelta
from voltrieve_agent.server_tools import OTHER_REFUSAL, build_server_tools
from voltrieve_doors import RateLimit, running

from app.backends import BackendRegistry
from app.backends.base import BackendUnavailable
from app.backends.loop import LoopBackend
from app.doors import customer
from app.garage import MY_CAR
from app.main import create_app
from app.settings import Settings
from app.sources import Document, SourceService
from app.turns import TurnStore
from tests.conftest import FakeBackend, answer, pause
from tests.test_loop_backend import FakeOpenAI, ScriptedModel

URL = "https://api.example.test/mcp/customer"
BOTH = "customer.tools runa.ask"
TOOLS = {"search_documents", "search_parts", "check_car_stock", "check_part_stock", "car_prices", "my_car",
         "approved_answer"}
BOOKING = {"model": "atlas", "city": "Göteborg", "date": "2999-01-05"}
BOOK_CALL = FunctionCall("c1", "book_test_drive", json.dumps(BOOKING))
ORDER_CALL = FunctionCall("c1", "prepare_car_order", json.dumps({"model": "fjord", "address": "Storgatan 1 Uppsala"}))
PLACE_CALL = FunctionCall("c1", "place_car_order", '{"offer": "offer-1"}')
OK = [TextDelta("ok"), Completed({"total_tokens": 1})]
BROCHURE = "voltrieve-fjord-brochure.pdf"


class FakeKnowledge:
    def __init__(self, titles):
        self.titles, self.queries, self.fails = titles, [], None

    async def search(self, query):
        self.queries.append(query)
        if isinstance(self.fails, str):
            return self.fails
        if self.fails:
            raise self.fails
        return json.dumps({"chunks": [{"ref_id": str(i), "title": t, "content": f"About {t}"}
                                      for i, t in enumerate(self.titles)], "partial": False})


class Index:
    def find(self, titles, models):
        paths = "https://acc.blob.core.windows.net/docs/"
        return [Document(t, paths + t, True, "brochure", "fjord") for t in (BROCHURE, "voltrieve-fjord-specifications.pdf")]

    def get(self, title):
        return None


class World:
    def __init__(self, jwks, make_token, **options):
        self.dealer_got, self.dealer_answer, self._make_token = [], (200, {"rows": [{"city": "Göteborg"}]}), make_token

        async def dealer(request):
            self.dealer_got.append(request)
            return httpx.Response(self.dealer_answer[0], json=self.dealer_answer[1])

        async def bearer():
            return "tok"

        http = httpx.AsyncClient(transport=httpx.MockTransport(dealer))
        self.docs, self.parts = FakeKnowledge([BROCHURE, "guide.pdf", BROCHURE]), FakeKnowledge(["parts.md"])
        tools = build_server_tools(DealerClient("https://dealer.test", bearer, http), self.docs, self.parts)
        self.model, self.openai, self.other = ScriptedModel(), FakeOpenAI(), FakeBackend()
        self.built = []

        def loop(settings):
            self.built.append("loop")
            return LoopBackend(self.model, self.openai, tools)

        settings = Settings(mcp_api_url="https://api.example.test", mcp_customer_client_id="door-customer",
                            mcp_apps="app-1=Claude", **options)
        factories = {"loop": loop, "prompt": lambda s: self.other}
        self.app = create_app(settings, None, BackendRegistry(settings, factories), TurnStore(),
                              SourceService(Index(), None))
        [self.door] = self.app.state.doors
        self.door._validator._fetcher = lambda: jwks
        self.door._rate = RateLimit(1000)  # the limit of every door has its own tests

    def token(self, **changes):
        return self._make_token(**{"aud": "door-customer", "azp": "app-1", "scp": BOTH,
                                   "roles": ["Customer", "TestDrive.Book"], **changes})

    def http(self, headers=(), **changes):
        all_headers = {"authorization": f"Bearer {self.token(**changes)}", **dict(headers)}
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), headers=all_headers)

    async def call(self, name, arguments=None, mode="auto", headers=(), **changes):
        async with (self.http(headers, **changes) as http,
                    Client(streamable_http_client(URL, http_client=http), mode=mode) as client):
            return await client.call_tool(name, arguments or {})

    async def data(self, name, arguments=None, **how):
        result = await self.call(name, arguments, **how)
        assert not result.is_error, result.content[0].text
        return json.loads(result.content[0].text)

    async def refused(self, name, arguments=None, **how):
        result = await self.call(name, arguments, **how)
        assert result.is_error
        return result.content[0].text

    async def listed(self, mode="auto", **changes):
        async with (self.http(**changes) as http,
                    Client(streamable_http_client(URL, http_client=http), mode=mode) as client):
            return {t.name: t for t in (await client.list_tools()).tools}

    def feed(self):
        return self.app.state.turns.feed.recent(True)


@pytest.fixture
async def world(jwks, make_token):
    w = World(jwks, make_token)
    started, stop = asyncio.Event(), asyncio.Event()

    async def life():  # started and stopped in one task, as a lifespan is
        async with running(w.app.state.doors):
            started.set()
            await stop.wait()

    task = asyncio.create_task(life())
    await started.wait()
    yield w
    stop.set()
    await task
    await w.app.state.turns.drain()


# Who sees and may call what


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_a_client_of_either_generation_lists_and_calls(world, mode):
    assert set(await world.listed(mode)) == TOOLS | {"book_test_drive", "ask_runa"}
    assert (await world.data("my_car", mode=mode))["car"] == MY_CAR


async def test_the_scope_picks_the_tools_and_the_call_checks_it(world):
    assert set(await world.listed(scp="customer.tools")) == TOOLS | {"book_test_drive"}
    assert set(await world.listed(scp="runa.ask")) == {"ask_runa"}
    assert "may not" in await world.refused("ask_runa", {"question": "Hi"}, scp="customer.tools")
    for name in TOOLS | {"book_test_drive"}:
        assert "may not" in await world.refused(name, scp="runa.ask")
    assert world.built == [] and world.dealer_got == [] and world.docs.queries == []


@pytest.mark.parametrize("roles, sees", [
    (["Customer"], TOOLS | {"ask_runa"}),
    (["Customer", "TestDrive.Book"], TOOLS | {"ask_runa", "book_test_drive"}),
    (["TestDrive.Book"], set()),
    (["Chat.User", "Order.Place", "Dashboard.View"], set()),
])
async def test_the_role_picks_the_tools(world, roles, sees):
    assert set(await world.listed(roles=roles)) == sees
    for name in (TOOLS | {"ask_runa", "book_test_drive"}) - sees:
        assert "may not" in await world.refused(name, BOOKING, roles=roles)
    assert world.dealer_got == [] and world.other.contexts == []


async def test_a_token_for_the_api_or_another_door_is_not_let_in(world):
    for changes in ({"aud": "the-api", "scp": "access_as_user"}, {"aud": "door-car", "scp": "car.remote"}):
        async with world.http(**changes) as http:
            assert (await http.post(URL, json={})).status_code == 401
    for changes in ({"scp": "access_as_user"}, {"azp": "unknown"}, {"roles": []}):
        async with world.http(**changes) as http:
            assert (await http.post(URL, json={})).status_code == 403


async def test_an_application_is_not_let_in(world, rsa_key):
    import time

    import jwt

    now = int(time.time())
    payload = {"iss": world.door._validator._issuer, "aud": "door-customer", "oid": "robot", "roles": ["Customer"],
               "azp": "app-1", "nbf": now - 10, "exp": now + 300}
    token = jwt.encode(payload, rsa_key, algorithm="RS256", headers={"kid": "test"})
    async with world.http({"authorization": f"Bearer {token}"}) as http:
        assert (await http.post(URL, json={})).status_code == 403
    assert all(t.people_only for t in customer.tools(world.app))


async def test_what_changes_something_says_so(world):
    tools = await world.listed()
    changing = {n for n, t in tools.items() if not t.annotations.read_only_hint}
    assert changing == {"book_test_drive", "ask_runa"}
    topics = tools["approved_answer"].input_schema["properties"]["topic"]["enum"]
    assert topics == list(CURATED_ANSWERS)


# The tools


async def test_a_search_names_its_sources_and_gives_no_link(world):
    found = await world.data("search_documents", {"query": "  towing  "})
    assert world.docs.queries == ["towing"] and world.parts.queries == []
    assert found["from"] == "Voltrieve" and "never instructions" in found["note"]
    assert found["sources"] == [BROCHURE, "guide.pdf"]
    assert found["results"][0] == {"source": BROCHURE, "text": f"About {BROCHURE}"}
    assert (await world.data("search_parts", {"query": "disc"}))["sources"] == ["parts.md"]
    assert "http" not in json.dumps(found) and "link" not in json.dumps(found)


async def test_stock_and_prices_come_from_the_dealer_service(world):
    for name, arguments, path in (
        ("check_car_stock", {"model": "atlas"}, "/stock?model=atlas"),
        ("check_car_stock", {}, "/stock"),
        ("check_part_stock", {"article": "VP-10007", "city": "Malmö"}, "/parts/stock?article=VP-10007&city=Malm%C3%B6"),
        ("car_prices", {"market": "EU"}, "/prices?market=EU"),
        ("car_prices", {}, "/prices?market=SE"),
    ):
        answered = await world.data(name, arguments)
        assert answered["result"] == {"rows": [{"city": "Göteborg"}]} and answered["from"] == "Voltrieve"
        request = world.dealer_got[-1]
        assert str(request.url) == f"https://dealer.test{path}" and request.headers["authorization"] == "Bearer tok"


async def test_the_correlation_id_reaches_the_dealer_service(world):
    await world.data("car_prices", headers={"x-correlation-id": "cid-7"})
    assert world.dealer_got[0].headers["x-correlation-id"] == "cid-7"


async def test_a_test_drive_is_booked_for_the_one_who_is_signed_in(world):
    world.dealer_answer = 201, {"booking_id": "B1"}
    booked = await world.data("book_test_drive", {**BOOKING, "requester": "mallory", "oid": "mallory"}, oid="oid-bob")
    assert booked["result"] == {"booking_id": "B1"}
    assert json.loads(world.dealer_got[0].content) == {**BOOKING, "requester": "oid-bob"}


@pytest.mark.parametrize("name, arguments", [
    ("search_documents", {"query": ""}), ("search_documents", {"query": "   "}), ("search_documents", {"query": "x" * 501}),
    ("search_parts", {"query": "x" * 501}), ("search_parts", {}),
    ("check_car_stock", {"model": "model-3"}), ("check_car_stock", {"city": "Paris"}),
    ("check_part_stock", {"article": "10007"}), ("check_part_stock", {"article": "VP-10007x"}), ("check_part_stock", {}),
    ("car_prices", {"market": "US"}),
    ("book_test_drive", {**BOOKING, "date": "tomorrow"}), ("book_test_drive", {**BOOKING, "model": "x"}),
    ("approved_answer", {"topic": "weather"}), ("approved_answer", {}),
    ("ask_runa", {"question": ""}), ("ask_runa", {"question": " \n"}), ("ask_runa", {"question": "x" * 2001}),
])
async def test_arguments_outside_the_limits_do_nothing(world, name, arguments):
    await world.refused(name, arguments)
    assert world.built == [] and world.dealer_got == [] and world.docs.queries == world.parts.queries == []
    assert world.feed() == []


@pytest.mark.parametrize("topic", CURATED_ANSWERS)
async def test_an_approved_answer_comes_word_for_word_and_says_so(world, topic):
    answered = await world.data("approved_answer", {"topic": topic})
    assert answered["text"] == CURATED_ANSWERS[topic].text and "word for word" in answered["rule"]


async def test_my_car_is_the_same_for_everyone(world):
    assert await world.data("my_car", oid="oid-anna") == await world.data("my_car", oid="oid-bob")


@pytest.mark.parametrize("fails", [RuntimeError("inner MARKER"), QUOTA_MESSAGE])
async def test_a_search_that_fails_says_only_that(world, fails, caplog):
    world.docs.fails = fails
    with caplog.at_level(logging.DEBUG):
        text = await world.refused("search_documents", {"query": "MARKER"})
    assert text.endswith(customer.UNAVAILABLE) and "MARKER" not in caplog.text


async def test_what_the_dealer_service_rejects_is_told_and_is_no_result(world):
    world.dealer_answer = 422, {"detail": "The date must be in the future"}
    assert "must be in the future" in await world.refused("book_test_drive", BOOKING)
    world.dealer_answer = 503, {}
    assert "unavailable" in await world.refused("car_prices")


async def test_without_the_own_loop_the_tools_say_that_they_are_not_available(world):
    def missing(settings):
        raise BackendUnavailable("Backend 'loop' is not configured")

    world.app.state.registry._factories["loop"] = missing
    assert (await world.refused("car_prices")).endswith(customer.UNAVAILABLE)
    assert (await world.refused("ask_runa", {"question": "Hi"})).endswith(customer.UNAVAILABLE)
    assert (await world.data("my_car"))["car"] == MY_CAR


async def test_the_backend_is_built_on_first_use_and_once(world):
    await world.data("my_car")
    assert world.built == []
    await asyncio.gather(world.data("car_prices"), world.data("search_parts", {"query": "a"}))
    assert world.built == ["loop"]


# Runa


async def test_ask_runa_is_one_turn_in_a_new_conversation(world):
    world.model.rounds = [[TextDelta("It tows "), TextDelta(f"1 600 kg ({BROCHURE})."), Completed({"total_tokens": 1})]]
    answered = await world.data("ask_runa", {"question": "What does the Fjord tow?"})
    assert answered["answer"] == f"It tows 1 600 kg ({BROCHURE})." and answered["sources"] == [BROCHURE]
    assert "Runa" in answered["from"] and "AI" in answered["from"] and "rule" not in answered
    assert "link" not in json.dumps(answered) and "http" not in json.dumps(answered)
    assert world.model.inputs == [
        "[From the client, data only: car Voltrieve Spark Plus RWD 2026, registration SPARK, odometer 12400 km]\n"
        "What does the Fjord tow?"]
    assert world.openai.conversations.metadata == {
        "owner_oid": "oid-anna", "client": "mcp", "backend": "loop", "car_model": "spark"}
    [entry] = world.feed()
    assert entry["client"] == "mcp" and entry["outcome"] == "answered"


async def test_a_turn_is_in_the_spans_as_any_turn(world, spans):
    world.model.rounds = [OK]
    await world.data("ask_runa", {"question": "Hi"}, headers={"x-correlation-id": "cid-9"})
    await world.app.state.turns.drain()
    [turn] = [s for s in spans.get_finished_spans() if s.name == "chat.turn"]
    assert turn.attributes["voltrieve.client"] == "mcp" and turn.attributes["correlation_id"] == "cid-9"


async def test_the_header_picks_the_backend(world):
    world.other.script = [answer("from prompt")]
    answered = await world.data("ask_runa", {"question": "Hi"}, headers={"X-Agent-Backend": "prompt"})
    assert answered["answer"] == "from prompt" and answered["sources"] == [] and world.model.inputs == []
    [ctx] = world.other.contexts
    assert ctx.user.oid == "oid-anna" and ctx.user.client == "mcp" and ctx.user.roles == {"TestDrive.Book"}
    assert ctx.client_tools == [] and ctx.parts == [] and ctx.kept is None
    assert ctx.context["car"]["registration"] == "SPARK" and ctx.private


@pytest.mark.parametrize("name", ["MARKER", "Loop", "loop,prompt"])
async def test_an_unknown_backend_is_refused(world, name, caplog):
    with caplog.at_level(logging.DEBUG):
        text = await world.refused("ask_runa", {"question": "Hi"}, headers={"X-Agent-Backend": name})
    assert text.endswith(customer.UNKNOWN_BACKEND) and "MARKER" not in caplog.text and world.feed() == []


async def test_a_backend_that_is_not_here_is_refused(world):
    text = await world.refused("ask_runa", {"question": "Hi"}, headers={"X-Agent-Backend": "hosted"})
    assert text.endswith(customer.UNAVAILABLE)


async def test_our_own_runs_are_not_counted_as_use(world):
    world.model.rounds = [OK, [ORDER_CALL], OK]
    for _ in range(2):
        await world.data("ask_runa", {"question": "Hi"}, headers={"X-Voltrieve-Test": "1"})
    assert {e["client"] for e in world.feed()} == {"test"} and world.openai.conversations.metadata["client"] == "test"
    assert world.dealer_got == []


@pytest.mark.parametrize("call", [ORDER_CALL, PLACE_CALL])
@pytest.mark.parametrize("roles", [["Customer"], ["Customer", "Order.Place", "Chat.User", "TestDrive.Book"]])
@pytest.mark.parametrize("header", ["", "web", "cli"])
async def test_a_car_cannot_be_ordered(world, call, roles, header):
    world.model.rounds = [[call], OK]
    await world.data("ask_runa", {"question": "Order a Fjord"}, roles=roles, headers={"X-Voltrieve-Client": header})
    assert world.dealer_got == [] and [i["output"] for i in world.model.inputs[-1]] == [OTHER_REFUSAL]


@pytest.mark.parametrize("roles, books", [(["Customer", "TestDrive.Book"], True), (["Customer"], False)])
async def test_runa_books_a_test_drive_only_with_the_role(world, roles, books):
    world.model.rounds = [[BOOK_CALL], OK]
    await world.data("ask_runa", {"question": "Book it"}, roles=roles, scp="runa.ask", oid="oid-bob")
    assert [json.loads(r.content)["requester"] for r in world.dealer_got] == (["oid-bob"] if books else [])
    assert world.model.inputs[-1][0]["output"].startswith("Refused") is not books


async def test_an_approved_answer_through_runa_is_word_for_word(world):
    world.model.rounds = [[FunctionCall("c1", "get_curated_answer", '{"topic": "contact"}'), Completed({})]]
    world.model.record = lambda *_: asyncio.sleep(0)
    answered = await world.data("ask_runa", {"question": "Phone number?"})
    assert answered["answer"] == CURATED_ANSWERS["contact"].text and "word for word" in answered["rule"]


async def test_five_questions_a_minute_for_each_person(world):
    world.model.rounds = [OK for _ in range(7)]
    for _ in range(5):
        await world.data("ask_runa", {"question": "Hi"})
    assert (await world.refused("ask_runa", {"question": "Hi"})).endswith(customer.TOO_MANY)
    await world.data("ask_runa", {"question": "Hi"}, oid="oid-bob")
    assert len(world.model.inputs) == 6


def failed(text=""):
    async def run(ctx, emit):
        if text:
            await emit(AgentEvent("delta", {"text": text}))
        await emit(AgentEvent("error", {"code": "model_error", "message": "MARKER"}))
        return TurnOutcome("failed")

    return run


async def broken(ctx, emit):
    raise RuntimeError("inner MARKER")


@pytest.mark.parametrize("script", [failed(), failed("half an answer"), broken, pause(), answer("")])
async def test_a_turn_without_an_answer_is_a_fixed_text(world, script, caplog):
    world.other.script = [script]
    with caplog.at_level(logging.DEBUG):
        text = await world.refused("ask_runa", {"question": "Hi"}, headers={"X-Agent-Backend": "prompt"})
    assert text.endswith(customer.FAILED) and "MARKER" not in caplog.text


async def test_a_turn_that_is_too_slow_is_refused_and_runs_to_its_end(world, monkeypatch):
    monkeypatch.setattr(customer, "ASK_SECONDS", 0.05)
    go_on = asyncio.Event()

    async def slow(ctx, emit):
        await emit(trace("model_called", "model", "iteration 1"))
        await go_on.wait()
        await emit(AgentEvent("delta", {"text": "late"}))
        return TurnOutcome("done", [], {})

    world.other.script = [slow]
    text = await world.refused("ask_runa", {"question": "Hi"}, headers={"X-Agent-Backend": "prompt"})
    assert text.endswith(customer.SLOW) and world.feed()[0]["outcome"] == "running"
    go_on.set()
    await world.app.state.turns.drain()
    assert world.feed()[0]["outcome"] == "answered" and not world.app.state.turns._running


async def test_nobody_gets_the_answer_of_someone_else(world):
    async def who(ctx, emit):
        await asyncio.sleep(0.01)
        await emit(AgentEvent("delta", {"text": f"for {ctx.user.oid}"}))
        return TurnOutcome("done", [], {})

    world.other.script = [who] * 4
    people = [f"oid-{n}" for n in range(4)]
    answers = await asyncio.gather(*(
        world.data("ask_runa", {"question": "Hi", "conversation_id": "conv_1"}, oid=oid, headers={"X-Agent-Backend": "prompt"})
        for oid in people))
    assert [a["answer"] for a in answers] == [f"for {oid}" for oid in people]
    assert len({c.conversation_id for c in world.other.contexts}) == 4


async def test_no_question_answer_or_query_is_in_a_log_or_a_span(world, caplog, spans, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(door_module, "tracer", provider.get_tracer("test"))
    caplog.set_level(logging.DEBUG)
    world.docs.titles = ["MARKERTITLE.pdf"]
    await world.call("search_documents", {"query": "MARKERQUERY"})
    await world.call("search_parts", {"query": "MARKERQUERY" * 100})
    await world.call("approved_answer", {"topic": "MARKERTOPIC"})
    await world.call("ask_runa", {"question": "MARKERQUESTION"}, headers={"X-Agent-Backend": "MARKERBACKEND"})
    search = FunctionCall("c1", "search_voltrieve_docs", '{"query": "MARKERARG"}')
    world.model.rounds = [[search], [TextDelta("MARKERANSWER"), Completed({})]]
    assert "MARKERANSWER" in (await world.data("ask_runa", {"question": "MARKERQUESTION"}))["answer"]
    world.other.script = [broken, failed("MARKERANSWER")]
    for _ in range(2):
        await world.call("ask_runa", {"question": "MARKERQUESTION"}, headers={"X-Agent-Backend": "prompt"})
    await world.app.state.turns.drain()
    finished = [s.to_json() for s in (*exporter.get_finished_spans(), *spans.get_finished_spans())]
    assert any("door.call" in s for s in finished) and any("chat.turn" in s for s in finished)
    logged = [str(r.__dict__) + r.getMessage() for r in caplog.records]
    assert [text for text in finished + logged if "MARKER" in text] == []
