"""The door to the car, through a real MCP client and a car page that is linked to the real app."""

import asyncio
import json
import logging
import os

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_carlink import CAR_APP, Page, car_app
from voltrieve_doors import running

from app.doors import car

HOST = "api.example.test"
TOOLS = {"car_status", "honk", "flash_lights", "set_wipers"}


@pytest.fixture
async def app(validator, jwks):
    app = car_app(validator, mcp_apps="claude-app=Claude;gpt-app=ChatGPT")
    for door in app.state.doors:
        door._validator._fetcher = lambda: jwks
    started, stop = asyncio.Event(), asyncio.Event()

    async def life():
        async with running(app.state.doors):
            started.set()
            await stop.wait()

    task = asyncio.create_task(life())
    await started.wait()
    yield app
    stop.set()
    await task


@pytest.fixture
def links(app):
    return app.state.car_links


@pytest.fixture
def page(app, make_token):
    return lambda oid="oid-anna": Page(app, make_token(oid=oid, azp=CAR_APP))


@pytest.fixture
def harness(app, make_token):
    """An AI of someone's own, signed in to the door."""

    class Harness:
        def __init__(self, **token):
            token = {"aud": "client-car", "scp": "car.remote", "roles": ["Car.Remote"], "azp": "claude-app", **token}
            self.token = make_token(**token)

        def http(self):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=f"https://{HOST}",
                                      headers={"authorization": f"Bearer {self.token}"})

        async def __aenter__(self):
            self.stack = self.http()
            await self.stack.__aenter__()
            self.client = Client(streamable_http_client(f"https://{HOST}/mcp/car", http_client=self.stack))
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *exc):
            await self.client.__aexit__(*exc)
            await self.stack.__aexit__(*exc)

        async def call(self, name, **arguments):
            result = await self.client.call_tool(name, arguments)
            return result.is_error, result.content[0].text

    return Harness


async def test_the_door_has_exactly_four_tools_and_none_of_them_names_a_car(harness):
    async with harness() as ai:
        tools = {t.name: t for t in (await ai.client.list_tools()).tools}
    assert set(tools) == TOOLS
    assert {n: set(t.input_schema.get("properties", {})) for n, t in tools.items()} == {
        "car_status": set(), "honk": {"times"}, "flash_lights": {"times"}, "set_wipers": {"mode"}}
    assert [n for n, t in tools.items() if t.annotations.read_only_hint] == ["car_status"]


@pytest.mark.parametrize("tool,arguments,says", [
    ("honk", {"times": 2}, "The car honked 2 times."),
    ("flash_lights", {"times": 1}, "The car flashes its lights 1 time."),
    ("set_wipers", {"mode": "slow"}, "The wipers are slow."),
])
async def test_a_command_from_the_ai_to_the_car_and_back(harness, page, tool, arguments, says):
    async with page() as car_page, harness(azp="gpt-app") as ai:
        await car_page.state(parked=True)
        call = asyncio.create_task(ai.call(tool, **arguments))
        command = await car_page.answer()
        assert {k: command[k] for k in ("name", "arguments", "app")} == {"name": tool, "arguments": arguments, "app": "ChatGPT"}
        assert await call == (False, says)


async def test_the_status(harness, page):
    async with harness() as ai:
        _, text = await ai.call("car_status")
        assert json.loads(text) == {"connected": False, "note": car.NOT_LINKED}
        async with page() as car_page:
            await car_page.state(parked=True, odometer_km=8214, wipers="off")
            for _ in range(8):  # it does not count as a command
                _, text = await ai.call("car_status")
            assert json.loads(text) == {"connected": True, "parked": True, "odometer_km": 8214, "wipers": "off"}
            assert car_page.idle


async def test_another_persons_car_does_not_exist(harness, page):
    async with page("oid-ben") as bens, harness() as ai:
        await bens.state(parked=True, odometer_km=8214)
        assert json.loads((await ai.call("car_status"))[1])["connected"] is False
        error, text = await ai.call("honk", times=1)
        assert error and car.NOT_LINKED in text
        for name in ("oid", "car", "link", "registration"):
            assert (await ai.call("honk", times=1, **{name: bens.id}))[0]
        assert bens.idle


@pytest.mark.parametrize("code", ["off", "moving", "unknown", "bad_arguments", "no_sound", "failed"])
async def test_what_the_car_says_becomes_our_own_text(harness, page, code):
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=True)
        call = asyncio.create_task(ai.call("honk", times=1))
        await car_page.answer(ok=False, message=code)
        error, text = await call
        assert error and car.REFUSALS[code] in text


async def test_every_code_has_a_text():
    from typing import get_args

    from app.carlink import CarCode

    assert set(get_args(CarCode)) | {"not_linked", "gone", "no_answer", "unsure", "too_many"} == set(car.REFUSALS)


async def test_a_car_that_moves_is_not_asked(harness, page):
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=False)
        error, text = await ai.call("flash_lights", times=1)
        assert error and car.NOT_PARKED in text and car_page.idle


async def test_a_car_that_does_not_answer(harness, page, links):
    links._take_seconds = 0.05
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=True)
        error, text = await ai.call("honk", times=1)
        assert error and car.NO_ANSWER in text


async def test_the_seventh_command_in_a_minute_is_refused(harness, page):
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=True)
        for _ in range(6):
            call = asyncio.create_task(ai.call("set_wipers", mode="off"))
            await car_page.answer()
            assert not (await call)[0]
        error, text = await ai.call("set_wipers", mode="off")
        assert error and car.REFUSALS["too_many"] in text and car_page.idle


@pytest.mark.parametrize("tool,arguments", [
    ("honk", {"times": 0}), ("honk", {"times": 4}), ("honk", {"times": "2"}), ("honk", {"times": 1.5}), ("honk", {"times": True}),
    ("flash_lights", {"times": 6}), ("set_wipers", {"mode": "max"}), ("set_wipers", {}),
])
async def test_arguments_outside_the_schema_never_reach_the_car(harness, page, tool, arguments):
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=True)
        assert (await ai.call(tool, **arguments))[0]
        assert car_page.idle


@pytest.mark.parametrize("name", ["set_speed", "park", "get_sign_photo", "honk ", "HONK", ""])
async def test_no_other_command_can_be_produced(harness, page, name):
    async with page() as car_page, harness() as ai:
        await car_page.state(parked=True)
        try:
            error = (await ai.call(name, kmh=50))[0]
        except Exception:
            error = True
        assert error and car_page.idle


@pytest.mark.parametrize("token", [
    {"roles": ["Chat.User"]}, {"roles": ["Dashboard.View"]},
])
async def test_the_role_is_needed_for_every_tool(harness, page, token):
    async with page() as car_page, harness(**token) as ai:
        await car_page.state(parked=True)
        assert (await ai.client.list_tools()).tools == []
        for name, arguments in (("car_status", {}), ("honk", {"times": 1}), ("flash_lights", {"times": 1}), ("set_wipers", {"mode": "off"})):
            assert await ai.call(name, **arguments) == (True, "You may not use this tool.")
        assert car_page.idle


@pytest.mark.parametrize("token,status", [
    ({"aud": os.environ["API_CLIENT_ID"], "scp": "access_as_user", "azp": CAR_APP}, 401),  # of the car itself
    ({"scp": "operations.read"}, 403),
    ({"azp": "someone-else"}, 403),
    ({"azp": CAR_APP}, 403),
])
async def test_other_tokens_do_not_open_the_door(harness, token, status):
    async with harness(**token).http() as c:
        r = await c.post("/mcp/car", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                         headers={"accept": "application/json, text/event-stream"})
    assert r.status_code == status


async def test_a_token_of_the_door_does_not_open_a_link(app, harness):
    async with harness().http() as c:
        assert (await c.post("/car/link")).status_code == 401


async def test_nothing_about_the_car_or_the_arguments_is_logged(harness, page, caplog):
    caplog.set_level(logging.DEBUG)
    async with page("oid-marker") as car_page, harness(oid="oid-marker") as ai:
        await car_page.state(parked=True, odometer_km=777123, wipers="fast")
        await ai.call("car_status")
        call = asyncio.create_task(ai.call("set_wipers", mode="fast"))
        await car_page.answer()
        await call
    records = [r for r in caplog.records if r.name.startswith(("app", "voltrieve"))]
    text = "\n".join(str(r.__dict__) + r.getMessage() for r in records)
    assert "door_call" in text and "set_wipers" in text
    for marker in ("777123", "oid-marker", "fast"):
        assert marker not in text
