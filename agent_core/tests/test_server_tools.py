import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from voltrieve_agent.dealer import DealerClient
from voltrieve_agent.knowledge import KnowledgeBase
from voltrieve_agent.server_tools import CAR_REFUSAL, ORDER_CLIENTS, ORDER_ROLE, build_server_tools
from voltrieve_agent.tools import ToolSet, UserContext, current_correlation_id, current_turn_id

BASE = "https://dealer.test"


class FakeKnowledgeBase:
    def __init__(self):
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return json.dumps({"chunks": [{"title": "Manual"}]})


@pytest.fixture
async def tools():
    async def token():
        return "tok"

    async with httpx.AsyncClient() as http:
        dealer = DealerClient(BASE, token, http)
        kb = FakeKnowledgeBase()
        yield ToolSet(build_server_tools(dealer, kb), []), kb


@pytest.fixture
def booker():
    return UserContext("oid-anna", "anna@example.com", frozenset({"TestDrive.Book"}))


@respx.mock
async def test_booking_requester_comes_from_user_not_model(tools, booker, events):
    route = respx.post(f"{BASE}/test-drives").respond(201, json={"booking_id": "B1"})
    args = {"model": "atlas", "city": "Stockholm", "date": "2999-01-01", "requester": "mallory"}
    out = await tools[0].execute("book_test_drive", json.dumps(args), booker, events)
    # the schema forbids a model-supplied requester, so the call is rejected outright
    assert out.startswith("Error: invalid arguments") and not route.called
    del args["requester"]
    out = await tools[0].execute("book_test_drive", json.dumps(args), booker, events)
    assert json.loads(out) == {"booking_id": "B1"}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"model": "atlas", "city": "Stockholm", "date": "2999-01-01", "requester": "oid-anna"}
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_422_becomes_readable_output(tools, booker, events):
    respx.post(f"{BASE}/test-drives").respond(422, json={"detail": "Invalid booking: no such city"})
    args = {"model": "atlas", "city": "Stockholm", "date": "2999-01-01"}
    out = await tools[0].execute("book_test_drive", json.dumps(args), booker, events)
    assert "rejected" in out and "no such city" in out


@respx.mock
async def test_5xx_becomes_readable_output(tools, user, events):
    respx.get(f"{BASE}/stock").respond(503)
    out = await tools[0].execute("check_stock", "{}", user, events)
    assert "unavailable" in out and "503" in out


@respx.mock
async def test_connection_error_becomes_readable_output(tools, user, events):
    respx.get(f"{BASE}/prices").mock(side_effect=httpx.ConnectError("nope"))
    out = await tools[0].execute("get_prices", "{}", user, events)
    assert "could not be reached" in out


@respx.mock
async def test_correlation_id_is_sent(tools, user, events):
    route = respx.get(f"{BASE}/stock").respond(200, json={"rows": []})
    reset = current_correlation_id.set("turn-42")
    try:
        await tools[0].execute("check_stock", '{"model": "atlas", "city": "Stockholm"}', user, events)
    finally:
        current_correlation_id.reset(reset)
    request = route.calls.last.request
    assert request.headers["x-correlation-id"] == "turn-42"
    assert dict(request.url.params) == {"model": "atlas", "city": "Stockholm"}


@respx.mock
async def test_correlation_id_generated_when_absent(tools, user, events):
    route = respx.get(f"{BASE}/prices").respond(200, json={})
    await tools[0].execute("get_prices", '{"market": "EU"}', user, events)
    assert route.calls.last.request.headers["x-correlation-id"]
    assert route.calls.last.request.url.params["market"] == "EU"


async def test_book_test_drive_requires_role(tools, user, events):
    args = '{"model": "atlas", "city": "Stockholm", "date": "2999-01-01"}'
    out = await tools[0].execute("book_test_drive", args, user, events)
    assert out.startswith("Refused")
    assert [e.data["step"] for e in events.events] == ["authorization_denied"]
    names = [d["name"] for d in tools[0].definitions()]
    assert "book_test_drive" in names and "search_voltrieve_docs" in names


async def test_search_tool_delegates_to_knowledge_base(tools, user, events):
    out = await tools[0].execute("search_voltrieve_docs", '{"query": "range"}', user, events)
    assert tools[1].queries == ["range"]
    assert json.loads(out)["chunks"][0]["title"] == "Manual"


async def test_knowledge_base_traces_sub_queries_and_titles(monkeypatch, user, events):
    from voltrieve_search.retrieve import Chunk, Reference, RetrieveResult

    result = RetrieveResult(
        chunks=[Chunk("0", "V1 Manual", "Range is 500 km")],
        references=[Reference("0", "k1", 2.0, None)],
        activity=[
            {"type": "modelQueryPlanning", "inputTokens": 10, "outputTokens": None,
             "model": {"modelName": "gpt-6-luna", "deploymentId": "gpt-6-luna"}},
            {"type": "agenticReasoning", "reasoningTokens": None},
            {"type": "searchIndex", "searchIndexArguments": {"search": "v1 range"}},
            {"type": "searchIndex", "searchIndexArguments": {"search": "battery capacity"}},
        ],
    )
    seen = {}

    def fake_retrieve(client, kb_name, question, knowledge_source):
        seen.update(kb_name=kb_name, question=question)
        return result

    monkeypatch.setattr("voltrieve_agent.knowledge.retrieve", fake_retrieve)
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="t"))
    kb = KnowledgeBase("https://search.test", credential)

    from voltrieve_agent.tools import ServerTool

    async def handler(args, u):
        return await kb.search(args["q"])

    ts = ToolSet(
        [ServerTool("s", "d", {"type": "object", "properties": {"q": {"type": "string"}}}, handler)],
        [],
    )
    out = await ts.execute("s", '{"q": "range?"}', user, events)
    assert seen == {"kb_name": "voltrieve-kb", "question": "range?"}
    assert json.loads(out)["chunks"] == [{"ref_id": "0", "title": "V1 Manual", "content": "Range is 500 km"}]
    (event,) = events.events
    assert event.data["step"] == "knowledge_base_retrieved"
    assert event.data["sub_queries"] == ["v1 range", "battery capacity"]
    assert event.data["references"] == ["V1 Manual"]
    assert event.data["planning_model"] == "gpt-6-luna"
    # A token count in the activity is optional, and can be null.
    assert event.data["planning_input_tokens"] == 10 and event.data["planning_output_tokens"] == 0
    assert event.data["search_reasoning_tokens"] == 0


def test_get_prices_says_it_holds_base_prices_only():
    description = next(t.description for t in build_server_tools(None, None) if t.name == "get_prices")
    assert description.startswith("Get the dealer's base price per model")
    assert "drive configuration" in description


async def test_current_date_is_the_date_in_stockholm(tools, user, events, monkeypatch):
    import datetime
    import voltrieve_agent.server_tools as server_tools

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            # 23:30 UTC on 4 November is already 5 November in Stockholm.
            return datetime.datetime(2026, 11, 4, 23, 30, tzinfo=datetime.UTC).astimezone(tz)

    monkeypatch.setattr(server_tools.datetime, "datetime", Clock)
    assert await tools[0].execute("get_current_date", "{}", user, events) == "2026-11-05"


def test_routing_descriptions_say_what_each_base_does_not_hold():
    tools = {t.name: t for t in build_server_tools(None, None)}
    assert "spare part article numbers are not here" in tools["search_voltrieve_docs"].description
    assert "does not say which parts a job needs" in tools["search_spare_parts"].description


def test_only_the_tools_that_change_something_run_alone():
    alone = [t.name for t in build_server_tools(None, None) if not t.concurrent]
    assert alone == ["book_test_drive", "prepare_car_order", "place_car_order"]


async def test_parts_search_uses_the_parts_knowledge_base(monkeypatch):
    from voltrieve_search.retrieve import RetrieveResult

    from voltrieve_agent import knowledge
    from voltrieve_agent.tools import ToolSet

    seen = {}

    def fake_retrieve(client, kb_name, question, knowledge_source):
        seen.update(kb=kb_name, source=knowledge_source, question=question)
        return RetrieveResult()

    class Credential:
        def get_token(self, scope):
            raise AssertionError("no token needed")

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    parts = knowledge.parts_knowledge_base("https://search.test", Credential())
    tools = ToolSet(build_server_tools(None, None, parts), [])
    events = []

    async def emit(event):
        events.append(event)

    user = UserContext("oid", "user", frozenset())
    await tools.execute("search_spare_parts", '{"query": "disc"}', user, emit)
    assert seen == {"kb": "voltrieve-parts-kb", "source": "voltrieve-parts-ks", "question": "disc"}
    assert events[0].data["knowledge_base"] == "voltrieve-parts-kb"
    assert events[0].data["detail"].startswith("voltrieve-parts-kb: ")


class _NoToken:
    def get_token(self, scope):
        raise AssertionError("no token needed")


async def test_search_waits_out_a_rate_limit(monkeypatch):
    from voltrieve_search.client import SearchApiError
    from voltrieve_search.retrieve import RetrieveResult

    from voltrieve_agent import knowledge

    answers = [SearchApiError(429, "busy"), RetrieveResult()]

    def fake_retrieve(client, kb_name, question, knowledge_source):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    monkeypatch.setattr(knowledge, "RATE_LIMIT_DELAY_SECONDS", 0)
    assert await knowledge.KnowledgeBase("https://s.test", _NoToken()).search("q")
    assert not answers


async def test_search_gives_up_after_the_retries(monkeypatch):
    from voltrieve_search.client import SearchApiError

    from voltrieve_agent import knowledge

    calls = []

    def fake_retrieve(client, kb_name, question, knowledge_source):
        calls.append(question)
        raise SearchApiError(429, "busy")

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    monkeypatch.setattr(knowledge, "RATE_LIMIT_DELAY_SECONDS", 0)
    with pytest.raises(SearchApiError):
        await knowledge.KnowledgeBase("https://s.test", _NoToken()).search("q")
    assert len(calls) == knowledge.RATE_LIMIT_RETRIES + 1


async def test_search_does_not_retry_other_failures(monkeypatch):
    from voltrieve_search.client import SearchApiError

    from voltrieve_agent import knowledge

    calls = []

    def fake_retrieve(client, kb_name, question, knowledge_source):
        calls.append(question)
        raise SearchApiError(403, "forbidden")

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    with pytest.raises(SearchApiError):
        await knowledge.KnowledgeBase("https://s.test", _NoToken()).search("q")
    assert len(calls) == 1


def test_docs_description_names_every_sales_document():
    docs = next(t for t in build_server_tools(None, None) if t.name == "search_voltrieve_docs").description
    for kind in ("brochure", "price list", "technical specifications", "accessories catalog",
                 "buyer's guide", "charging guide", "warranty", "service guide", "financing"):
        assert kind in docs, kind


def test_part_stock_tool_schema_and_description():
    from voltrieve_agent.server_tools import CITIES

    tool = next(t for t in build_server_tools(None, None) if t.name == "check_part_stock")
    params = tool.parameters
    assert params["required"] == ["article"]
    assert params["properties"]["article"]["pattern"] == "^VP-\\d{5}$"
    assert params["properties"]["city"]["enum"] == CITIES
    assert "search_spare_parts" in tool.description


@respx.mock
async def test_part_stock_tool_calls_the_dealer(tools, booker, events):
    route = respx.get(f"{BASE}/parts/stock").respond(200, json={"article": "VP-10007", "rows": []})
    out = await tools[0].execute("check_part_stock", json.dumps({"article": "VP-10007", "city": "Stockholm"}),
                                 booker, events)
    assert json.loads(out)["article"] == "VP-10007"
    assert dict(route.calls.last.request.url.params) == {"article": "VP-10007", "city": "Stockholm"}


@respx.mock
async def test_part_stock_tool_rejects_a_city_or_article_the_dealer_does_not_know(tools, booker, events):
    route = respx.get(f"{BASE}/parts/stock").respond(
        404, json={"detail": "Unknown article: VP-00001, not a Voltrieve article number"})
    bad_city = await tools[0].execute("check_part_stock", json.dumps({"article": "VP-10007", "city": "Gothenburg"}),
                                      booker, events)
    bad_form = await tools[0].execute("check_part_stock", json.dumps({"article": "10007"}), booker, events)
    assert bad_city.startswith("Error: invalid arguments") and bad_form.startswith("Error: invalid arguments")
    assert not route.called
    unknown = await tools[0].execute("check_part_stock", json.dumps({"article": "VP-00001"}), booker, events)
    assert "not a Voltrieve article number" in unknown


def test_parts_description_names_the_new_catalogs():
    parts = next(t for t in build_server_tools(None, None) if t.name == "search_spare_parts").description
    for kind in ("tyres", "interior", "body", "lighting"):
        assert kind in parts, kind


ADDRESS = "Storgatan 1, 753 20 Uppsala"
ORDER_TOOLS = ("prepare_car_order", "place_car_order")
ORDER_CALLS = {
    "prepare_car_order": json.dumps({"model": "fjord", "address": ADDRESS}),
    "place_car_order": json.dumps({"offer": "offer-1"}),
}


def order_user(client="web", *roles):
    return UserContext("oid-anna", "anna@example.com", frozenset(roles or {"Order.Place"}), "Anna A", client)


@pytest.fixture
def buyer():
    return order_user()


@pytest.fixture
def in_turn():
    reset = current_turn_id.set("turn-7")
    yield
    current_turn_id.reset(reset)


def order_tools():
    return [t for t in build_server_tools(None, None) if t.name in ORDER_TOOLS]


def test_the_order_tools_are_private_run_alone_and_need_the_role_and_a_client():
    assert CAR_REFUSAL == (
        "It is not possible to order a car from within the car. "
        "You are welcome to talk with Runa about buying a car on the Voltrieve website."
    )
    assert ORDER_CLIENTS == frozenset({"web", "cli"}) and ORDER_ROLE == "Order.Place"
    assert [t.name for t in order_tools()] == list(ORDER_TOOLS)
    for tool in order_tools():
        assert tool.private and not tool.concurrent and tool.required_role == ORDER_ROLE
        assert tool.clients == ORDER_CLIENTS and tool.client_refusal == CAR_REFUSAL
    others = [t for t in build_server_tools(None, None) if t.name not in ORDER_TOOLS]
    assert all(t.clients is None and not t.private for t in others)


def test_the_schemas_descriptions_and_statuses_of_the_order_tools():
    from voltrieve_agent.server_tools import MODEL_SLUGS

    prepare, place = order_tools()
    assert prepare.parameters["properties"]["model"]["enum"] == MODEL_SLUGS
    assert prepare.parameters["properties"]["address"] == {"type": "string", "minLength": 5, "maxLength": 200}
    assert set(prepare.parameters["properties"]) == {"model", "address"}
    assert prepare.parameters["required"] == ["model"] and prepare.parameters["additionalProperties"] is False
    assert place.parameters == {
        "type": "object", "properties": {"offer": {"type": "string", "maxLength": 40}},
        "required": ["offer"], "additionalProperties": False,
    }
    assert prepare.status == ("preparing_order", "Preparing your order")
    assert place.status == ("placing_order", "Placing your order")
    assert prepare.description == (
        "Call this first when the user wants to buy or order a car, before you ask for anything. Without an "
        "address it gives the price and the stock. With the address the user gave, it makes an offer to read back."
    )
    assert place.description == (
        "Order the car of an offer. Only when the user has said yes to the offer in a later message."
    )


@respx.mock
async def test_prepare_without_an_address_gives_the_terms_and_asks_for_it(tools, buyer, events, in_turn):
    route = respx.get(f"{BASE}/orders/terms").respond(200, json={"model": "fjord", "price": 529000})
    out = await tools[0].execute("prepare_car_order", '{"model": "fjord"}', buyer, events)
    assert dict(route.calls.last.request.url.params) == {"model": "fjord"} and len(respx.calls) == 1
    assert '"price":529000' in out.replace(" ", "")
    assert "Ask the user for the delivery address" in out and "saved with the order" in out


@respx.mock
async def test_no_address_is_asked_for_when_the_terms_fail(tools, buyer, events, in_turn):
    respx.get(f"{BASE}/orders/terms").respond(503)
    out = await tools[0].execute("prepare_car_order", '{"model": "fjord"}', buyer, events)
    assert "unavailable" in out and "address" not in out


@respx.mock
async def test_prepare_with_an_address_posts_who_and_when_from_the_context(tools, buyer, events, in_turn):
    route = respx.post(f"{BASE}/orders/offers").respond(201, json={"offer": "offer-1", "price": 529000})
    out = await tools[0].execute("prepare_car_order", ORDER_CALLS["prepare_car_order"], buyer, events)
    assert json.loads(out) == {"offer": "offer-1", "price": 529000} and len(respx.calls) == 1
    request = route.calls.last.request
    assert json.loads(request.content) == {
        "model": "fjord", "address": ADDRESS, "customer": "oid-anna", "name": "Anna A", "turn": "turn-7",
        "client": "web",
    }
    assert str(request.url) == f"{BASE}/orders/offers" and "Storgatan" not in str(request.headers)


@respx.mock
async def test_place_posts_the_offer_the_customer_and_the_turn(tools, buyer, events, in_turn):
    route = respx.post(f"{BASE}/orders").respond(201, json={"order_number": "VO-1001"})
    out = await tools[0].execute("place_car_order", ORDER_CALLS["place_car_order"], buyer, events)
    assert json.loads(out) == {"order_number": "VO-1001"}
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"offer": "offer-1", "customer": "oid-anna", "turn": "turn-7"}


@pytest.mark.parametrize("extra", ["customer", "name", "turn", "client"])
@pytest.mark.parametrize("name", ORDER_TOOLS)
async def test_the_model_cannot_say_who_orders_or_when(tools, buyer, events, name, extra):
    arguments = json.loads(ORDER_CALLS[name]) | {extra: "mallory"}
    out = await tools[0].execute(name, json.dumps(arguments), buyer, events)
    assert out == f"Error: invalid arguments for '{name}': arguments: additionalProperties False"


@respx.mock
@pytest.mark.parametrize("client", ["car", "other", "test", ""])
@pytest.mark.parametrize("name", ORDER_TOOLS)
async def test_the_car_and_unknown_clients_never_reach_the_dealer(tools, events, name, client):
    out = await tools[0].execute(name, ORDER_CALLS[name], order_user(client, "Chat.User"), events)
    assert out == CAR_REFUSAL and len(respx.calls) == 0
    assert [e.data["step"] for e in events.events] == ["client_not_allowed"]


@respx.mock
@pytest.mark.parametrize("name", ORDER_TOOLS)
async def test_the_order_tools_need_the_role(tools, events, name):
    out = await tools[0].execute(name, ORDER_CALLS[name], order_user("web", "TestDrive.Book"), events)
    assert out.startswith("Refused") and len(respx.calls) == 0


@respx.mock
async def test_a_read_timeout_on_placing_says_that_the_order_may_exist(tools, buyer, events, in_turn):
    route = respx.post(f"{BASE}/orders").mock(side_effect=httpx.ReadTimeout("slow"))
    out = await tools[0].execute("place_car_order", ORDER_CALLS["place_car_order"], buyer, events)
    assert out == "The order may have been received. Call place_car_order again with the same offer."
    assert route.call_count == 1


@respx.mock
async def test_other_failures_give_the_text_of_today(tools, buyer, events, in_turn):
    unreachable = "The dealer service could not be reached ({}). Try again later."
    respx.post(f"{BASE}/orders").mock(side_effect=httpx.ConnectError("no"))
    out = await tools[0].execute("place_car_order", ORDER_CALLS["place_car_order"], buyer, events)
    assert out == unreachable.format("ConnectError")
    respx.post(f"{BASE}/orders/offers").mock(side_effect=httpx.ReadTimeout("slow"))
    out = await tools[0].execute("prepare_car_order", ORDER_CALLS["prepare_car_order"], buyer, events)
    assert out == unreachable.format("ReadTimeout")


@respx.mock
async def test_a_refusal_of_the_dealer_reaches_the_model_and_no_log(tools, buyer, events, in_turn, caplog):
    respx.post(f"{BASE}/orders").respond(409, json={"detail": f"The customer has not answered yet. {ADDRESS}"})
    with caplog.at_level("DEBUG"):
        out = await tools[0].execute("place_car_order", ORDER_CALLS["place_car_order"], buyer, events)
    assert "HTTP 409" in out and "has not answered yet" in out
    assert ADDRESS not in caplog.text and events.events == []
