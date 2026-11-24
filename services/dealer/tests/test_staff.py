"""The routes and the live events of the representatives."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from test_orders import ADDRESS, NAME, UNAVAILABLE, BrokenStore, Clock, log, make_offer, place

from app import staff
from app.hub import QUEUE_SIZE
from app.main import create_app

ROUTES = [
    ("GET", "/staff/state"), ("POST", "/staff/stock"), ("POST", "/staff/orders/VO-1001/deliver"),
    ("DELETE", "/staff/orders/VO-1001"), ("POST", "/staff/reset"), ("GET", "/staff/events"),
]  # fmt: skip


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(settings, validator, staff_validator, clock):
    return create_app(settings, validator, staff_validator=staff_validator, clock=clock)


def client_of(app, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=headers)


@pytest.fixture
def staff_token(make_token):
    def make(**claims):
        claims = {"scp": "manage_as_user", "name": "Bo Exempel", **claims}
        return make_token(roles=["Dealer.Manage"], claims={k: v for k, v in claims.items() if v is not None})

    return make


@pytest.fixture
async def rep(app, staff_token):
    async with client_of(app, staff_token()) as c:
        yield c


@pytest.fixture
async def runa(app, make_token):
    async with client_of(app, make_token()) as c:
        yield c


async def an_order(runa, /, **changes) -> str:
    offer = await make_offer(runa, **changes)
    return (await place(runa, offer)).json()["order_number"]


@pytest.mark.parametrize("method, path", ROUTES)
async def test_who_may_not(app, make_token, staff_token, method, path):
    async with client_of(app) as c:
        assert (await c.request(method, path)).status_code == 401
        bad = await c.request(method, path, headers={"Authorization": "Bearer not-a-jwt"})
        assert bad.status_code == 401 and bad.headers["Cache-Control"] == "no-store"
    assert log(app) == []
    tokens = [
        make_token(),  # Runa
        make_token(roles=["Dealer.Manage"]),  # the role without a person
        make_token(roles=[], claims={"scp": "manage_as_user"}),  # a person without the role
        make_token(roles=["Dealer.Manage"], claims={"scp": "other"}),
    ]
    for token in tokens:
        async with asyncio.timeout(5), client_of(app, token) as c:  # an open stream has no end
            assert (await c.request(method, path)).status_code == 403
    assert [(e["kind"], e["who"]) for e in log(app)] == [("refused", None)] * 4


async def test_a_cookie_opens_nothing(app, staff_token):
    async with client_of(app) as c:
        r = await c.post("/staff/reset", headers={"Cookie": f"token={staff_token()}; Authorization={staff_token()}"})
    assert r.status_code == 401


async def test_the_state(rep, runa, clock):
    first = await an_order(runa)
    clock.now += timedelta(minutes=1)
    second = await an_order(runa, model="spark", client="cli")
    r = await rep.get("/staff/state")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert set(body) == {"models", "cities", "stock", "orders", "log"}
    assert body["models"][0] == {"slug": "spark", "name": "Spark", "price": 289000}
    assert [m["name"] for m in body["models"]] == ["Spark", "Lumen", "Ragn", "Fjord", "Atlas"]
    assert body["cities"] == ["Stockholm", "Göteborg", "Malmö", "Uppsala"]
    assert list(body["stock"]) == ["spark", "lumen", "ragn", "fjord", "atlas"]
    assert body["stock"]["fjord"] == {"Stockholm": 1, "Göteborg": 4, "Malmö": 3, "Uppsala": 2}
    assert [o["number"] for o in body["orders"]] == [second, first]
    assert body["orders"][1] == {
        "number": first, "model": "fjord", "price_sek": 529000, "name": NAME, "address": ADDRESS, "client": "web",
        "status": "open", "created": "2026-11-24T12:00:00+00:00", "city": None, "delivered": None,
        "delivered_by": None,
    }  # fmt: skip
    assert [e["kind"] for e in body["log"]] == ["offer", "order", "offer", "order"]


async def test_reading_the_state_is_not_logged(app, rep):
    await rep.get("/staff/state")
    assert log(app) == []


async def test_the_state_purges_orders_over_30_days(rep, runa, clock):
    await an_order(runa)
    clock.now += timedelta(days=30) - timedelta(seconds=1)
    assert len((await rep.get("/staff/state")).json()["orders"]) == 1
    clock.now += timedelta(seconds=1)
    assert (await rep.get("/staff/state")).json()["orders"] == []


async def test_the_stock(app, rep):
    r = await rep.post("/staff/stock", json={"model": "fjord", "city": "Stockholm", "change": -1})
    assert (r.status_code, r.json()) == (200, {"in_stock": 0})
    r = await rep.post("/staff/stock", json={"model": "fjord", "city": "Stockholm", "change": -1})
    assert (r.status_code, r.json()) == (409, {"detail": "There is none in stock in Stockholm."})
    r = await rep.post("/staff/stock", json={"model": "fjord", "city": "Stockholm", "change": 1})
    assert r.json() == {"in_stock": 1}
    found = [(e["kind"], e["model"], e["city"], e["result"], e["who"]) for e in log(app)]
    assert found == [("stock_changed", "fjord", "Stockholm", n, "Bo Exempel") for n in ("0", "1")]


@pytest.mark.parametrize(
    "changes",
    [{"model": "Marker"}, {"city": "Marker"}, {"change": 2}, {"change": 0}, {"change": "1"},
     {"change": 1.5}, {"change": None}],
)  # fmt: skip
async def test_a_bad_stock_change_is_422(app, rep, changes):
    r = await rep.post("/staff/stock", json={"model": "fjord", "city": "Malmö", "change": 1, **changes})
    assert r.status_code == 422 and "Marker" not in r.text and r.headers["Cache-Control"] == "no-store"
    assert log(app) == []


async def test_a_delivery(app, rep, runa, clock):
    number = await an_order(runa)
    clock.now += timedelta(hours=1)
    r = await rep.post(f"/staff/orders/{number}/deliver", json={"city": "Malmö"})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    order = r.json()
    assert (order["status"], order["city"], order["delivered_by"]) == ("delivered", "Malmö", "Bo Exempel")
    assert order["delivered"] == "2026-11-24T13:00:00+00:00"
    state = (await rep.get("/staff/state")).json()
    assert state["orders"] == [order] and state["stock"]["fjord"]["Malmö"] == 2
    again = await rep.post(f"/staff/orders/{number}/deliver", json={"city": "Malmö"})
    assert (again.status_code, again.json()) == (409, {"detail": "This order is already delivered."})
    (entry,) = log(app, "delivered")
    assert (entry["who"], entry["model"], entry["city"], entry["number"]) == ("Bo Exempel", "fjord", "Malmö", number)


async def test_a_delivery_without_stock(app, rep, runa):
    number = await an_order(runa, model="ragn")
    r = await rep.post(f"/staff/orders/{number}/deliver", json={"city": "Malmö"})
    assert (r.status_code, r.json()) == (409, {"detail": "There is none in stock in Malmö."})
    assert log(app, "delivered") == []


async def test_an_unknown_order_or_city(app, rep, runa):
    number = await an_order(runa)
    before = log(app)
    for unknown in ("VO-9999", "Marker", "VO-1001%0A"):
        for r in [
            await rep.post(f"/staff/orders/{unknown}/deliver", json={"city": "Malmö"}),
            await rep.delete(f"/staff/orders/{unknown}"),
        ]:
            assert (r.status_code, r.json()) == (404, {"detail": "Unknown order."})
    r = await rep.post(f"/staff/orders/{number}/deliver", json={"city": "Marker"})
    assert r.status_code == 422 and "Marker" not in r.text
    assert log(app) == before


async def test_a_delete(app, rep, runa):
    number = await an_order(runa)
    r = await rep.delete(f"/staff/orders/{number}")
    assert (r.status_code, r.content, r.headers["Cache-Control"]) == (204, b"", "no-store")
    assert (await rep.delete(f"/staff/orders/{number}")).status_code == 404
    (entry,) = log(app, "deleted")
    assert (entry["who"], entry["model"], entry["number"]) == ("Bo Exempel", "fjord", number)


async def test_a_reset(app, rep, runa):
    await an_order(runa)
    await rep.post("/staff/stock", json={"model": "fjord", "city": "Malmö", "change": -1})
    queue = app.state.hub.subscribe()
    r = await rep.post("/staff/reset")
    assert (r.status_code, r.content) == (204, b"")
    state = (await rep.get("/staff/state")).json()
    assert state["orders"] == [] and state["stock"]["fjord"]["Malmö"] == 3
    assert [(e["kind"], e["who"]) for e in state["log"]] == [("reset", "Bo Exempel")]
    assert [queue.get_nowait()[0] for _ in range(queue.qsize())] == ["clear", "log", "changed"]


async def test_each_change_is_one_entry_and_one_changed(app, rep, runa):
    numbers = [await an_order(runa) for _ in range(2)]
    queue = app.state.hub.subscribe()
    for call in [
        rep.post("/staff/stock", json={"model": "atlas", "city": "Malmö", "change": 1}),
        rep.post(f"/staff/orders/{numbers[0]}/deliver", json={"city": "Malmö"}),
        rep.delete(f"/staff/orders/{numbers[1]}"),
    ]:
        await call
        assert [queue.get_nowait()[0] for _ in range(queue.qsize())] == ["log", "changed"]
    for refused in [
        rep.post(f"/staff/orders/{numbers[0]}/deliver", json={"city": "Malmö"}),
        rep.delete(f"/staff/orders/{numbers[1]}"),
    ]:
        await refused
        assert queue.empty()
    entries = json.dumps(app.state.hub.latest(), ensure_ascii=False)
    assert ADDRESS not in entries and NAME not in entries


@pytest.mark.parametrize(
    "claims, who",
    [
        ({}, "Bo Exempel"),
        ({"name": None, "preferred_username": "bo@example.com"}, "bo@example.com"),
        ({"name": None}, "Representative"),
    ],
)
async def test_who_made_the_change(app, staff_token, claims, who):
    async with client_of(app, staff_token(**claims)) as c:
        await c.post("/staff/reset")
    assert log(app)[0]["who"] == who


async def test_without_a_store(settings, validator, staff_validator, staff_token):
    app = create_app(settings, validator, staff_validator=staff_validator, store=BrokenStore())
    bodies = {"/staff/stock": {"model": "fjord", "city": "Malmö", "change": 1}}
    async with client_of(app, staff_token()) as c:
        for method, path in ROUTES[:-1]:
            r = await c.request(method, path, json=bodies.get(path, {"city": "Malmö"}))
            assert (r.status_code, r.json()) == (503, {"detail": UNAVAILABLE}), path
            assert r.headers["Cache-Control"] == "no-store"
    assert app.state.hub.latest() == []


class Stream:
    """One open event stream, read as the server sends it: a test client waits for the end."""

    def __init__(self):
        self.status, self.headers, self.text = None, {}, ""
        self.news, self.left = asyncio.Event(), asyncio.Event()
        self.task: asyncio.Task | None = None
        self.asked = False

    async def receive(self):
        if not self.asked:
            self.asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self.left.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
        else:
            self.text += message.get("body", b"").decode()
        self.news.set()

    async def until(self, done):
        async with asyncio.timeout(5):
            while not done(self.text):
                self.news.clear()
                await self.news.wait()

    def events(self):
        return [line.removeprefix("event: ") for line in self.text.splitlines() if line.startswith("event: ")]


@asynccontextmanager
async def open_stream(app, token):
    stream = Stream()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/staff/events", "raw_path": b"/staff/events", "query_string": b"", "root_path": "",
        "headers": [(b"authorization", f"Bearer {token}".encode())], "client": ("c", 1), "server": ("t", 80),
    }  # fmt: skip
    stream.task = asyncio.create_task(app(scope, stream.receive, stream.send))
    try:
        await stream.until(lambda text: text)
        yield stream
    finally:
        stream.left.set()
        await asyncio.wait_for(stream.task, 5)


def followers(app):
    return len(app.state.hub._queues)


async def test_the_stream_sends_what_happens_after_it_opened(app, staff_token, rep, runa):
    await runa.get("/prices")
    async with open_stream(app, staff_token()) as stream:
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-store"
        await an_order(runa)
        await rep.post("/staff/reset")
        await stream.until(lambda text: text.count("event: changed\n") == 2)
        assert stream.events() == ["log", "log", "changed", "clear", "log", "changed"]
        assert "event: changed\ndata: {}\n\n" in stream.text
        data = [line.removeprefix("data: ") for line in stream.text.splitlines() if line.startswith("data: ")]
        assert json.loads(data[1]) == {**json.loads(data[1]), "kind": "order", "number": "VO-1001", "who": "Runa"}
        assert "prices" not in stream.text and ADDRESS not in stream.text


async def test_the_stream_sends_a_keepalive(app, staff_token, monkeypatch):
    assert staff.KEEPALIVE_SECONDS == 15
    monkeypatch.setattr(staff, "KEEPALIVE_SECONDS", 0.01)
    async with open_stream(app, staff_token()) as stream:
        await stream.until(lambda text: text.count(": keepalive\n\n") >= 3)
        assert stream.events() == []


async def test_the_stream_ends_after_30_minutes(app, staff_token, clock, monkeypatch):
    monkeypatch.setattr(staff, "KEEPALIVE_SECONDS", 0.01)
    async with open_stream(app, staff_token()) as stream:
        clock.now += timedelta(minutes=29, seconds=59)
        seen = stream.text.count("keepalive")
        await stream.until(lambda text: text.count("keepalive") > seen + 2)
        assert not stream.task.done()
        clock.now += timedelta(seconds=1)
        await asyncio.wait_for(asyncio.shield(stream.task), 5)
        assert followers(app) == 0


async def test_the_stream_ends_after_30_minutes_also_when_busy(app, staff_token, clock):
    async with open_stream(app, staff_token()) as stream:
        clock.now += timedelta(minutes=30)
        app.state.hub.changed()
        await asyncio.wait_for(asyncio.shield(stream.task), 5)


async def test_the_queue_is_freed_when_the_client_leaves(app, staff_token):
    async with open_stream(app, staff_token()):
        assert followers(app) == 1
    assert followers(app) == 0


async def test_a_dropped_stream_ends(app, staff_token):
    async with open_stream(app, staff_token()) as stream:
        for _ in range(QUEUE_SIZE + 1):
            app.state.hub.changed()
        assert followers(app) == 0
        await asyncio.wait_for(asyncio.shield(stream.task), 5)
        assert len(stream.events()) < QUEUE_SIZE


async def test_a_stream_that_was_dropped_while_it_waited_ends_at_the_keepalive(app, staff_token, monkeypatch):
    monkeypatch.setattr(staff, "KEEPALIVE_SECONDS", 0.01)
    async with open_stream(app, staff_token()) as stream:
        app.state.hub._queues.clear()
        await asyncio.wait_for(asyncio.shield(stream.task), 5)


async def test_two_streams_get_the_same(app, staff_token):
    async with open_stream(app, staff_token()) as one, open_stream(app, staff_token()) as two:
        app.state.hub.changed()
        for stream in (one, two):
            await stream.until(lambda text: "event: changed" in text)
