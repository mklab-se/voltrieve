"""The order routes, the log of every call, and what must never leave the service."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from app.data import today
from app.main import create_app
from app.model import StoreUnavailable
from app.store import MemoryStore
from app.telemetry import instrument_app

ADDRESS = "Marköravägen 7, 753 20 Uppsala"
NAME = "Ada Markörsson"
OFFER = {"model": "fjord", "address": ADDRESS, "customer": "oid-1", "name": NAME, "turn": "t1", "client": "web"}
UNAVAILABLE = "The dealer system is not available right now."


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


class BrokenStore:
    """A store that answers nothing."""

    def __getattr__(self, name):
        async def broken(*_):
            raise StoreUnavailable("the store gave no answer")

        return broken


class CountingStore(MemoryStore):
    """A store that is away at first."""

    away = True
    seeds = 0

    async def seed(self, stock):
        if self.away:
            raise StoreUnavailable("the store gave no answer")
        self.seeds += 1
        await super().seed(stock)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(settings, validator, clock):
    return create_app(settings, validator, clock=clock)


def client_of(app, headers):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers)


@pytest.fixture
async def client(app, auth_header):
    async with client_of(app, auth_header) as c:
        yield c


async def make_offer(client, /, **changes) -> str:
    r = await client.post("/orders/offers", json={**OFFER, **changes})
    assert r.status_code == 201, r.text
    return r.json()["offer"]


async def place(client, offer, customer="oid-1", turn="t2"):
    return await client.post("/orders", json={"offer": offer, "customer": customer, "turn": turn})


def log(app, kind=None):
    return [e for e in app.state.hub.latest() if kind in (None, e["kind"])]


async def test_the_terms(client, app):
    r = await client.get("/orders/terms", params={"model": "fjord"})
    assert r.status_code == 200
    assert r.json() == {
        "model": "fjord", "price": 529000, "currency": "SEK",
        "stock": {"Stockholm": 1, "Göteborg": 4, "Malmö": 3, "Uppsala": 2},
    }  # fmt: skip
    await app.state.store.change_stock("fjord", "Malmö", -1)
    assert (await client.get("/orders/terms", params={"model": "fjord"})).json()["stock"]["Malmö"] == 2
    assert list(r.json()["stock"]) == ["Stockholm", "Göteborg", "Malmö", "Uppsala"]


async def test_an_order_takes_an_offer_and_a_later_turn(client):
    r = await client.post("/orders/offers", json={**OFFER, "address": " Marköravägen 7,\n753 20 Uppsala "})
    assert r.status_code == 201
    offer = r.json()["offer"]
    assert r.json() == {
        "offer": offer, "model": "fjord", "price": 529000, "currency": "SEK", "name": NAME, "address": ADDRESS,
    }  # fmt: skip
    first, again = await place(client, offer), await place(client, offer, turn="t3")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == {
        "order_number": "VO-1001", "model": "fjord", "price": 529000, "currency": "SEK", "name": NAME,
        "address": ADDRESS, "ordered": "2026-11-24T12:00:00+00:00",
        "message": "Thank you for your order. Voltrieve will be in touch about delivery.",
    }  # fmt: skip
    # The same order, and Runa is told so: otherwise "one more" would look like a new one.
    assert again.json() == first.json() | {
        "message": "This offer was already placed. It is the same order as before, not a new one. Another car needs "
                   "a new offer, also of the same model and to the same address.",
    }  # fmt: skip
    assert (await place(client, await make_offer(client))).json()["order_number"] == "VO-1002"


async def test_half_a_character_in_an_address_is_no_error_of_ours(client):
    body = json.dumps({**OFFER, "address": "Gatan 12 \ud800"})
    r = await client.post("/orders/offers", content=body, headers=JSON)
    assert (r.status_code, r.json()["address"]) == (201, "Gatan 12")


async def test_the_same_turn_is_409(client, app):
    r = await place(client, await make_offer(client), turn="t1")
    assert (r.status_code, r.json()) == (
        409,
        {"detail": "The customer has not answered yet. Read back the car, the price and the address, and ask."},
    )


async def test_an_expired_offer_is_410(client, clock):
    offer = await make_offer(client)
    clock.now += timedelta(minutes=30)
    r = await place(client, offer)
    assert (r.status_code, r.json()) == (
        410,
        {"detail": "The offer has expired. Nothing was ordered. Make a new one with prepare_car_order, read it back "
                   "and ask."},
    )


@pytest.mark.parametrize("customer, offer", [("oid-2", None), ("oid-1", "unknown"), ("oid-1", "a/b#?|c")])
async def test_an_unknown_offer_is_404(client, customer, offer):
    r = await place(client, offer or await make_offer(client), customer=customer)
    assert (r.status_code, r.json()) == (
        404,
        {"detail": "Unknown offer, or it has expired. Nothing was ordered. Make a new one with prepare_car_order, "
                   "read it back and ask."},
    )


LONG = "x" * 201
JSON = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "MarkerModel"}, {"address": "Mark"}, {"address": "Marker" * 900}, {"address": "\n\t \n"},
        {"customer": LONG}, {"customer": " "}, {"name": LONG}, {"turn": ""}, {"turn": " "}, {"turn": None},
        {"turn": LONG}, {"client": "MarkerCar"}, {"client": "car"}, {"client": ""}, {"address": ["Marker"]},
    ],
    ids=lambda c: f"{next(iter(c))}",
)  # fmt: skip
async def test_a_bad_offer_is_422_without_the_value(client, app, changes):
    r = await client.post("/orders/offers", json={**OFFER, **changes})
    assert r.status_code == 422
    if next(iter(changes)) != "model":  # that one is checked by the route
        assert [error["loc"] for error in r.json()["detail"]] == [["body", next(iter(changes))]]
    for value in (NAME, ADDRESS, "Mark", LONG, "oid-1"):
        assert value not in r.text
    assert log(app) == []


@pytest.mark.parametrize("field", ["offer", "customer", "turn"])
@pytest.mark.parametrize("value", ["", "  ", None, LONG])
async def test_a_bad_placement_is_422_without_the_value(client, app, field, value):
    body = {"offer": "Marker-offer", "customer": "Marker-oid", "turn": "Marker-turn", field: value}
    r = await client.post("/orders", json=body)
    assert r.status_code == 422 and "Marker" not in r.text and LONG not in r.text
    assert r.json()["detail"][0]["loc"] == ["body", field]
    assert set(r.json()["detail"][0]) == {"loc", "msg"}
    assert log(app) == []


async def test_no_validation_error_shows_the_input(client):
    """On every route, also the old ones."""
    for r in [
        await client.get("/orders/terms", params={"model": "Marker"}),
        await client.get("/orders/terms", params={"Marker": "Marker"}),
        await client.get("/prices", params={"market": "Marker"}),
        await client.post("/test-drives", json={"model": ["Marker"], "city": {"Marker": 1}, "date": "Marker"}),
        await client.post("/orders/offers", content='{"address": "Marker', headers=JSON),
        await client.post("/orders/offers", json="Marker"),
        await client.post("/orders/offers", json={"Marker": "Marker"}),
    ]:
        assert r.status_code == 422 and "Marker" not in r.text, r.text


async def test_an_unknown_key_is_not_shown(app, auth_header):
    class Strict(BaseModel, extra="forbid"):
        model: str

    @app.post("/strict")
    def strict(body: Strict) -> None: ...

    async with client_of(app, auth_header) as c:
        r = await c.post("/strict", json={"model": "spark", ADDRESS: 1})
    assert r.status_code == 422 and ADDRESS not in r.text
    assert r.json()["detail"][0]["loc"] == ["body"]


async def test_the_stock_route_reads_the_store(client, app):
    await client.get("/stock")  # seeds
    await app.state.store.change_stock("atlas", "Göteborg", -1)
    r = await client.get("/stock", params={"model": "atlas"})
    assert r.json() == {
        "rows": [
            {"model": "atlas", "city": city, "in_stock": count}
            for city, count in [("Stockholm", 2), ("Göteborg", 2), ("Malmö", 1), ("Uppsala", 5)]
        ]
    }


async def test_the_stock_keeps_the_order_of_the_data(settings, validator, auth_header, client):
    """The table gives its rows sorted by key."""

    class Sorted(MemoryStore):
        async def stock(self):
            found = await super().stock()
            return {model: dict(sorted(found[model].items())) for model in sorted(found)}

    app = create_app(settings, validator, store=Sorted())
    async with client_of(app, auth_header) as c:
        assert (await c.get("/stock")).json() == (await client.get("/stock")).json()
    rows = (await client.get("/stock")).json()["rows"]
    assert [r["model"] for r in rows[::4]] == ["spark", "lumen", "ragn", "fjord", "atlas"]
    assert [r["city"] for r in rows[:4]] == ["Stockholm", "Göteborg", "Malmö", "Uppsala"]


async def test_every_kind_of_call_is_logged_once(client, app, auth_header):
    client.headers["X-Correlation-Id"] = "cid-5"
    date = (today() + timedelta(days=3)).isoformat()
    article = next(iter(app.state.data.articles))
    await client.get("/stock", params={"model": "atlas", "city": "Göteborg"})
    await client.get("/parts/stock", params={"article": article, "city": "Malmö"})
    await client.get("/prices")
    await client.get("/orders/terms", params={"model": "fjord"})
    offer = await make_offer(client)
    await place(client, offer)
    await client.post(
        "/test-drives", json={"model": "atlas", "city": "Malmö", "date": date, "requester": "Marker-oid"}
    )
    await client.get("/healthz")
    entries = log(app)
    assert [e["kind"] for e in entries] == ["stock", "part_stock", "prices", "terms", "offer", "order", "test_drive"]
    assert all(e["who"] == "Runa" and e["correlation_id"] == "cid-5" for e in entries)
    stock, parts, _, terms, made, order, drive = entries
    assert (stock["model"], stock["city"], stock["result"]) == ("atlas", "Göteborg", "3")
    assert parts["city"] == "Malmö" and parts["result"].isdigit()
    assert (terms["model"], made["model"], made["client"], made["number"]) == ("fjord", "fjord", "web", None)
    assert (order["model"], order["client"], order["number"], order["result"]) == ("fjord", "web", "VO-1001", None)
    assert (drive["model"], drive["city"]) == ("atlas", "Malmö")
    text = json.dumps(entries, ensure_ascii=False)
    for secret in (ADDRESS, "Marköra", NAME, "oid-1", "Marker", offer):
        assert secret not in text


async def test_what_was_not_ordered_is_logged_with_a_fixed_text(client, app, clock):
    offer = await make_offer(client)
    await place(client, offer, turn="t1")
    await place(client, "Marker")
    clock.now += timedelta(minutes=30)
    await place(client, offer)
    orders = log(app, "order")
    assert [e["result"] for e in orders] == [
        "The customer has not answered yet", "Unknown offer", "The offer has expired",
    ]  # fmt: skip
    assert all(e["number"] is None for e in orders) and "Marker" not in json.dumps(log(app))


async def test_every_placement_tells_the_open_pages(client, app):
    """Also a repeated one: the answer to the first may have been lost, before it told them."""
    offer = await make_offer(client)
    queue = app.state.hub.subscribe()
    await place(client, offer)
    await place(client, offer)
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [name for name, _ in items] == ["log", "changed"] * 2
    assert [e["result"] for e in log(app, "order")] == [None, "Already placed"]


async def test_an_unknown_value_is_not_logged(client, app):
    await client.get("/stock", params={"model": "Marker"})
    await client.get("/parts/stock", params={"article": "Marker", "city": "Marker"})
    assert log(app) == []


async def test_a_caller_without_the_role_is_logged_and_one_without_a_token_is_not(app, make_token):
    async with client_of(app, {}) as c:
        assert (await c.get("/orders/terms", params={"model": "fjord"})).status_code == 401
        for token in ("not-a-jwt", make_token(exp_delta_seconds=-60), make_token(aud="other", roles=[])):
            r = await c.post("/orders", json={}, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401
        assert log(app) == []
        r = await c.get("/stock", headers={"Authorization": f"Bearer {make_token(roles=['Other'])}"})
    assert r.status_code == 403
    (entry,) = log(app)
    assert (entry["kind"], entry["who"], entry["result"]) == ("refused", None, None)


@pytest.mark.parametrize("roles", [["Dealer.Manage"], ["Dealer.Manage", "Dealer.Access"], ["Dealer.Access"]])
async def test_a_person_gets_403_on_every_machine_route(app, make_token, roles):
    token = make_token(roles=roles, claims={"scp": "manage_as_user", "name": "Bo"})
    async with client_of(app, {"Authorization": f"Bearer {token}"}) as c:
        for method, path in [
            ("GET", "/stock"), ("GET", "/parts/stock"), ("GET", "/prices"), ("POST", "/test-drives"),
            ("GET", "/orders/terms"), ("POST", "/orders/offers"), ("POST", "/orders"),
        ]:  # fmt: skip
            assert (await c.request(method, path)).status_code == 403, path


async def test_without_a_store(settings, validator, auth_header, capsys):
    app = create_app(settings, validator, store=BrokenStore())
    date = (today() + timedelta(days=3)).isoformat()
    async with client_of(app, auth_header) as c:
        assert (await c.get("/healthz")).status_code == 200
        for r in [
            await c.get("/stock"),
            await c.get("/orders/terms", params={"model": "fjord"}),
            await c.post("/orders/offers", json=OFFER),
            await place(c, "unknown"),
        ]:
            assert (r.status_code, r.json()) == (503, {"detail": UNAVAILABLE})
        assert (await c.get("/prices")).status_code == 200
        drive = {"model": "atlas", "city": "Malmö", "date": date, "requester": "x"}
        assert (await c.post("/test-drives", json=drive)).status_code == 201
    out = capsys.readouterr().out
    assert "/orders/offers" in out and ADDRESS not in out


async def test_startup_seeds_and_purges(settings, validator, auth_header, clock):
    store = MemoryStore()
    app = create_app(settings, validator, store=store, clock=clock)
    async with client_of(app, auth_header) as c:
        order = await place(c, await make_offer(c))
        clock.now += timedelta(days=30)
        restarted = create_app(settings, validator, store=store, clock=clock)
        async with restarted.router.lifespan_context(restarted):
            await restarted.state.keeper.ready()
            assert order.status_code == 201 and await store.orders() == []
            assert (await store.stock())["fjord"]["Malmö"] == 3


async def test_a_store_that_is_away_at_startup_is_tried_again(settings, validator, auth_header, capsys):
    store = CountingStore()
    app = create_app(settings, validator, store=store)
    async with app.router.lifespan_context(app):
        async with client_of(app, auth_header) as c:
            assert (await c.get("/stock")).status_code == 503
            assert (await c.get("/healthz")).status_code == 200
            store.away = False
            assert (await c.get("/stock")).json()["rows"][0]["in_stock"] == 4
            await c.get("/stock")
    assert store.seeds == 1
    assert "StoreUnavailable" in capsys.readouterr().out


class Failing(MemoryStore):
    """A store that cannot purge at first."""

    away, purges, seeds = True, 0, 0

    async def seed(self, stock):
        self.seeds += 1
        await super().seed(stock)

    async def purge(self, now):
        self.purges += 1
        if self.away:
            raise StoreUnavailable("the store gave no answer")
        await super().purge(now)


async def test_a_failing_purge_closes_nothing(settings, validator, auth_header, clock, capsys):
    store = Failing()
    app = create_app(settings, validator, store=store, clock=clock)
    async with client_of(app, auth_header) as c:
        assert (await c.get("/stock")).status_code == 200
        assert (await c.get("/orders/terms", params={"model": "fjord"})).status_code == 200
        assert (await place(c, await make_offer(c))).status_code == 201
        assert (store.seeds, store.purges) == (1, 1)
        assert "The old orders were not removed: StoreUnavailable" in capsys.readouterr().out
        clock.now += timedelta(minutes=5) - timedelta(seconds=1)
        await c.get("/stock")
        assert store.purges == 1  # not tried again yet
        clock.now += timedelta(seconds=1)
        await c.get("/stock")
        await c.get("/stock")
        assert store.purges == 2
        store.away = False
        clock.now += timedelta(minutes=5)
        for _ in range(2):
            assert (await c.get("/stock")).status_code == 200
        assert (store.seeds, store.purges) == (1, 3)  # it worked, so a read of the stock does not purge again


async def test_the_next_offer_removes_the_expired_ones(app, client, clock):
    old = await make_offer(client)
    clock.now += timedelta(minutes=30)
    await make_offer(client)
    clock.now -= timedelta(minutes=30)
    assert (await place(client, old)).status_code == 404  # gone, not just expired


async def test_the_lifespan_closes_the_store(settings, validator):
    class Closing(MemoryStore):
        closed = False

        async def close(self):
            self.closed = True

    store = Closing()
    app = create_app(settings, validator, store=store)
    async with app.router.lifespan_context(app):
        assert not store.closed
    assert store.closed


async def test_nothing_personal_reaches_a_log_line_or_a_span(settings, validator, auth_header, clock, capsys):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings, validator, clock=clock)
    instrument_app(app, tracer_provider=provider)
    try:
        async with client_of(app, auth_header) as c:
            await c.get("/orders/terms", params={"model": "fjord"})
            offer = await make_offer(c)
            assert (await place(c, offer, turn="t1")).status_code == 409
            assert (await place(c, offer)).status_code == 201
            assert (await c.post("/orders/offers", json={**OFFER, "turn": ""})).status_code == 422
            app.state.store = BrokenStore()
            assert (await c.post("/orders/offers", json=OFFER)).status_code == 503
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    spans = "".join(span.to_json() for span in exporter.get_finished_spans())
    lines = capsys.readouterr().out
    assert "/orders/offers" in spans and '"/orders/offers"' in lines
    for secret in (ADDRESS, "Mark", NAME, "oid-1", json.dumps(ADDRESS)[1:-1], json.dumps(NAME)[1:-1]):
        assert secret not in spans and secret not in lines
