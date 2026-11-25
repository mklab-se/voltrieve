"""The links of the cars: who may open one, who may speak for it, and how a command is answered."""

import asyncio
import json
import logging

import httpx
import pytest

from app import carlink
from app.carlink import CarLinks
from app.clients import AZURE_CLI_CLIENT_ID
from app.main import create_app
from app.settings import Settings

CAR_APP = "car-app"


class Page:
    """A car page with remote control on: the stream of its link, through the real app."""

    def __init__(self, app, token: str):
        self.app, self.auth = app, {"Authorization": f"Bearer {token}"}
        self.status, self.headers, self.raw = 0, {}, b""
        self._gone, self._events = asyncio.Event(), asyncio.Queue()

    async def __aenter__(self):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "POST",
            "path": "/car/link", "raw_path": b"/car/link", "query_string": b"", "scheme": "http", "root_path": "",
            "headers": [(b"host", b"test"), (b"authorization", self.auth["Authorization"].encode())],
            "client": ("127.0.0.1", 1), "server": ("test", 80),
        }
        asked = False

        async def receive():
            nonlocal asked
            if not asked:
                asked = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._gone.wait()
            return {"type": "http.disconnect"}

        buffer = b""

        async def send(message):
            nonlocal buffer
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
                return
            self.raw += message.get("body", b"")
            buffer += message.get("body", b"")
            while b"\n\n" in buffer:
                block, _, buffer = buffer.partition(b"\n\n")
                if not block.startswith(b":"):
                    name, data = block.decode().split("\n")
                    self._events.put_nowait((name[7:], json.loads(data[6:])))

        self.task = asyncio.create_task(self.app(scope, receive, send))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=self.auth)
        name, data = await self.next()
        assert name == "link"
        self.id = data["id"]
        return self

    async def __aexit__(self, *exc):
        await self.leave()
        await self.http.aclose()

    async def leave(self):
        self._gone.set()
        await self.task

    async def next(self, seconds=2.0):
        return await asyncio.wait_for(self._events.get(), seconds)

    @property
    def idle(self) -> bool:
        return self._events.empty()

    async def state(self, **state):
        return await self.http.post(f"/car/link/{self.id}/state", json=state)

    async def result(self, command, ok=True, **more):
        return await self.http.post(f"/car/link/{self.id}/results", json={"id": command["id"], "ok": ok, **more})

    async def answer(self, ok=True, **more) -> dict:
        """Take the next command and answer it."""
        name, command = await self.next()
        assert name == "command"
        assert (await self.result(command, ok, **more)).status_code == 204
        return command


def car_app(validator, **settings):
    return create_app(
        Settings(mcp_api_url="https://api.example.test", mcp_car_client_id="client-car", car_client_id=CAR_APP, **settings),
        validator,
    )


@pytest.fixture
def app(validator):
    return car_app(validator)


@pytest.fixture
def links(app) -> CarLinks:
    return app.state.car_links


@pytest.fixture
def page(app, make_token):
    def _page(oid="oid-anna", **token):
        return Page(app, make_token(oid=oid, azp=CAR_APP, **token))

    return _page


async def honk(links, oid="oid-anna", app="Claude"):
    return await links.command(oid, "honk", {"times": 1}, app)


# ---- opening a link


async def test_the_link_is_a_private_stream_whose_first_event_is_its_id(page):
    async with page() as car:
        assert car.status == 200
        assert car.headers["content-type"].startswith("text/event-stream")
        assert car.headers["cache-control"] == "no-store" and car.headers["x-accel-buffering"] == "no"
        assert len(car.id) >= 16


@pytest.mark.parametrize("azp", ["web-app", "cli", "", AZURE_CLI_CLIENT_ID])
async def test_only_the_car_may(client, make_token, azp):
    auth = {"Authorization": f"Bearer {make_token(azp=azp)}"}
    for path, body in (("/car/link", None), ("/car/link/x/state", {"parked": True}), ("/car/link/x/results", {"id": "1", "ok": True})):
        assert (await client.post(path, json=body, headers=auth)).status_code == 403


async def test_no_client_is_the_car_when_the_setting_is_empty(validator, make_token):
    app = create_app(Settings(mcp_api_url="https://h", mcp_car_client_id="c", car_client_id=""), validator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for azp in ("", CAR_APP):
            r = await c.post("/car/link", headers={"Authorization": f"Bearer {make_token(azp=azp)}"})
            assert r.status_code == 403


async def test_the_azure_cli_may_when_it_is_allowed(validator, make_token):
    app = car_app(validator, mcp_allow_cli=True)
    async with Page(app, make_token(azp=AZURE_CLI_CLIENT_ID)) as car:
        assert (await car.state(parked=True)).status_code == 204


async def test_a_token_is_needed(client, make_token):
    assert (await client.post("/car/link")).status_code == 401
    assert (await client.post("/car/link/x/state", json={"parked": True})).status_code == 401
    expired = {"Authorization": f"Bearer {make_token(azp=CAR_APP, exp_delta_seconds=-60)}"}
    assert (await client.post("/car/link", headers=expired)).status_code == 401
    no_role = {"Authorization": f"Bearer {make_token(azp=CAR_APP, roles=['Other'])}"}
    assert (await client.post("/car/link", headers=no_role)).status_code == 403


async def test_without_the_door_there_are_no_links(validator, make_token):
    app = create_app(Settings(car_client_id=CAR_APP), validator)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/car/link", headers={"Authorization": f"Bearer {make_token(azp=CAR_APP)}"})
    assert r.status_code == 404


# ---- who may speak for a link


async def test_someone_elses_link_is_as_unknown_as_one_that_never_was(page, links):
    async with page() as anna, page("oid-ben") as ben:
        await anna.state(parked=True)
        waiting = asyncio.create_task(honk(links))
        _, command = await anna.next()
        for path, body in (("state", {"parked": False}), ("results", {"id": command["id"], "ok": True})):
            theirs = await ben.http.post(f"/car/link/{anna.id}/{path}", json=body)
            unknown = await ben.http.post(f"/car/link/nothing/{path}", json=body)
            assert theirs.status_code == unknown.status_code == 404 and theirs.json() == unknown.json()
        # An answer on their own link to a command of the other's is ignored.
        assert (await ben.result(command)).status_code == 204
        assert not waiting.done() and links.get("oid-anna", anna.id).parked
        await anna.result(command)
        assert await waiting == "done"


async def test_a_command_reaches_its_owners_car_only(page, links):
    async with page() as anna, page("oid-ben") as ben:
        await anna.state(parked=True)
        await ben.state(parked=True)
        waiting = asyncio.create_task(honk(links, "oid-ben", "ChatGPT"))
        command = await ben.answer()
        assert {k: v for k, v in command.items() if k != "id"} == {"name": "honk", "arguments": {"times": 1}, "app": "ChatGPT"}
        assert await waiting == "done" and anna.idle
        assert await honk(links, "oid-carl") == "not_linked"


async def test_of_two_links_the_one_opened_last_answers(page, links):
    async with page() as first:
        await first.state(parked=True)
        async with page() as second:
            await second.state(parked=True)
            waiting = asyncio.create_task(honk(links))
            await second.answer()
            assert await waiting == "done" and first.idle
            await second.state(parked=False)
            assert await honk(links) == "moving"
        waiting = asyncio.create_task(honk(links))
        await first.answer()
        assert await waiting == "done"


# ---- refusals


async def test_a_car_that_has_not_said_that_it_is_parked_is_not(page, links):
    async with page() as car:
        assert await honk(links) == "moving"
        await car.state(parked=False)
        assert await honk(links) == "moving"
        assert car.idle


async def test_the_car_says_no(page, links):
    async with page() as car:
        await car.state(parked=True)
        for code in ("off", "moving", "unknown", "bad_arguments", "no_sound", "failed"):
            waiting = asyncio.create_task(honk(links, "oid-anna"))
            await car.answer(ok=False, message=code)
            assert await waiting == code


async def test_a_no_without_a_reason(page, links):
    async with page() as car:
        await car.state(parked=True)
        waiting = asyncio.create_task(honk(links))
        await car.answer(ok=False)
        assert await waiting == "failed"


async def test_six_commands_a_minute_for_each_person(page, links, clock):
    links._rate._clock = clock
    async with page() as car, page("oid-ben") as ben:
        await car.state(parked=True)
        await ben.state(parked=True)

        async def one(which=car, oid="oid-anna"):
            waiting = asyncio.create_task(honk(links, oid))
            await which.answer()
            return await waiting

        assert [await one() for _ in range(6)] == ["done"] * 6
        assert await honk(links) == "too_many" and car.idle
        assert await one(ben, "oid-ben") == "done"
        clock.now += 61
        assert await one() == "done"


async def test_a_refusal_before_the_car_is_asked_does_not_count(page, links):
    assert [await honk(links) for _ in range(10)] == ["not_linked"] * 10
    async with page() as car:
        assert [await honk(links) for _ in range(10)] == ["moving"] * 10
        await car.state(parked=True)
        waiting = asyncio.create_task(honk(links))
        await car.answer()
        assert await waiting == "done"


async def test_no_answer_in_time_and_a_late_one_is_ignored(page, links):
    links._answer_seconds = 0.05
    async with page() as car:
        await car.state(parked=True)
        assert await honk(links) == "no_answer"
        _, command = await car.next()
        assert (await car.result(command)).status_code == 204
        links._answer_seconds = 2
        waiting = asyncio.create_task(honk(links))
        _, second = await car.next()
        await car.result(command)  # the old one once more
        assert not waiting.done()
        await car.result(second, ok=False, message="moving")
        await car.result(second)  # a second answer changes nothing
        assert await waiting == "moving"
        assert links.get("oid-anna", car.id).waiting == {}


async def test_a_command_that_nobody_waits_for_is_not_sent(links):
    links._answer_seconds = 0.01
    link = links.open("oid-anna", 60)
    link.parked = True
    stream = links.events(link)
    await anext(stream)
    assert await honk(links) == "no_answer"  # the stream was slow to take it
    links.close(link)
    assert b"event: end" in await anext(stream)


async def test_a_cancelled_call_leaves_nothing(page, links):
    async with page() as car:
        await car.state(parked=True)
        waiting = asyncio.create_task(honk(links))
        _, command = await car.next()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert links.get("oid-anna", car.id).waiting == {}
        assert (await car.result(command)).status_code == 204


# ---- the end of a link


async def test_a_car_that_leaves_takes_its_link_and_its_waiting_calls_along(page, links):
    async with page() as car:
        await car.state(parked=True)
        waiting = asyncio.create_task(honk(links))
        await car.next()
        await car.leave()
        assert await asyncio.wait_for(waiting, 1) == "gone"
        assert await honk(links) == "not_linked"
        assert (await car.state(parked=True)).status_code == 404
        assert links._links == {}


async def test_nothing_is_kept_for_a_car_that_comes_later(page, links):
    assert await honk(links) == "not_linked"
    async with page() as car:
        await car.state(parked=True)
        with pytest.raises(TimeoutError):
            await car.next(0.05)


async def test_a_link_ends_with_the_token(page):
    async with page(exp_delta_seconds=2) as car:
        name, data = await car.next(4)
        assert (name, data) == ("end", {"reason": "expired"})
        await asyncio.wait_for(car.task, 1)


async def test_a_link_ends_after_half_an_hour(app, links, clock, make_token):
    links._clock = clock
    async with Page(app, make_token(azp=CAR_APP, exp_delta_seconds=7200)) as car:
        await car.state(parked=True)
        clock.now += 1799
        assert links.latest("oid-anna") is not None
        clock.now += 1
        assert await honk(links) == "not_linked"
        assert (await car.state(parked=True)).status_code == 404


async def test_the_stream_is_kept_alive(page, monkeypatch):
    assert carlink.KEEPALIVE_SECONDS <= 15
    monkeypatch.setattr(carlink, "KEEPALIVE_SECONDS", 0.01)
    async with page() as car:
        await asyncio.sleep(0.1)
        assert car.raw.count(b": keep-alive\n\n") > 1


async def test_three_links_for_each_person_and_the_oldest_goes(page, links):
    async with page("oid-ben") as ben, page() as first, page() as second, page() as third, page() as fourth:
        assert await first.next() == ("end", {"reason": "replaced"})
        assert (await first.state(parked=True)).status_code == 404
        for other, oid in ((ben, "oid-ben"), (second, "oid-anna"), (third, "oid-anna"), (fourth, "oid-anna")):
            assert links.get(oid, other.id)


def test_fifty_links_in_all_and_the_oldest_goes(links):
    opened = [links.open(f"oid-{n}", 60) for n in range(51)]
    assert opened[0].closed == "replaced" and links.latest("oid-0") is None
    assert all(not link.closed for link in opened[1:])


# ---- what the car may say


BAD_STATES = [{}, {"parked": 1}, {"parked": "true"}, {"parked": True, "speed_kmh": 3}, {"parked": True, "odometer_km": -1},
              {"parked": True, "odometer_km": 1.5}, {"parked": True, "odometer_km": True}, {"parked": True, "wipers": "MARKER"},
              {"parked": True, "registration": "MARKER"}, ["MARKER"]]
BAD_RESULTS = [{}, {"id": "1"}, {"id": "1", "ok": "yes"}, {"id": "1", "ok": False, "message": "MARKER do this"},
               {"id": "MARKER MARKER", "ok": True}, {"id": "1", "ok": True, "app": "MARKER"}, {"id": 1, "ok": True}]


@pytest.mark.parametrize("path,body", [("state", b) for b in BAD_STATES] + [("results", b) for b in BAD_RESULTS])
async def test_a_body_that_is_not_as_agreed_is_422_and_is_not_repeated(page, path, body):
    async with page() as car:
        r = await car.http.post(f"/car/link/{car.id}/{path}", json=body)
        assert r.status_code == 422
        # Only the name of a field that does not belong there can come back, as on the other routes.
        assert "MARKER" not in r.text


async def test_a_state_keeps_what_it_does_not_mention(page, links):
    async with page() as car:
        await car.state(parked=True, odometer_km=8214, wipers="slow")
        await car.state(parked=False)
        link = links.get("oid-anna", car.id)
        assert (link.parked, link.odometer_km, link.wipers) == (False, 8214, "slow")


async def test_the_log_knows_no_person_no_car_and_no_command(page, links, caplog):
    caplog.set_level(logging.DEBUG)
    async with page("oid-marker") as car:
        await car.state(parked=True, odometer_km=777123, wipers="fast")
        waiting = asyncio.create_task(links.command("oid-marker", "set_wipers", {"mode": "fast"}, "Claude"))
        await car.answer()
        assert await waiting == "done"
    text = "\n".join(str(r.__dict__) + r.getMessage() for r in caplog.records if not r.name.startswith("asyncio"))
    assert "Car link opened" in text
    for marker in ("oid-marker", "777123", "set_wipers", "fast"):
        assert marker not in text
