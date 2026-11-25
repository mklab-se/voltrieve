# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["httpx", "httpx2", "mcp==2.3.0"]
# ///
"""The deployed MCP doors, from a scripted MCP client: what works and what is refused.

    uv run scripts/doors_check.py [--doors operations,car,dealer,customer,runa] [--ask] [--out FILE]

It signs in as you, with tokens from the az CLI, so MCP_ALLOW_CLI must be true in the environment and you
need the roles of the doors. It changes the stock of your own city by one and back. It plays a car of its
own (POST /car/link) for the car's door. It never delivers an order and never books. `--ask` also asks
Runa one question (about 0.1 SEK).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import httpx2
from mcp import Client
from mcp import types as t
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voltrieve_client import TEST_HEADER, _az_token, api_token, api_url, azd_value, parse_sse  # noqa: E402

EXPECTED = {
    "operations": {"service_health", "recent_turns", "problem_turns", "explain_turn", "what_people_ask"},
    "car": {"car_status", "honk", "flash_lights", "set_wipers"},
    "dealer": {"my_stock", "change_stock", "open_orders", "deliver_order"},
    "runa": {"ask_runa"},
}
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream",
           "mcp-protocol-version": "2025-11-25"}
PERSONAL = ("MARKER", "voltrieve.user", "prompt\":", "@")
lines: list[str] = []
failed = 0


def note(text: str) -> None:
    lines.append(text)
    print(text, flush=True)


def check(name: str, passed: bool, detail: str = "") -> None:
    global failed
    failed += not passed
    note(f"{'ok    ' if passed else 'FAILED'} {name}{': ' + detail if detail else ''}")


def base(door: str) -> str:
    name = "MCP_DEALER_URL" if door == "dealer" else "MCP_API_URL"
    try:
        return azd_value(name).rstrip("/")
    except SystemExit:
        return azd_value("DEALER_URL" if door == "dealer" else "API_URL").rstrip("/")


def token(door: str) -> str:
    client_id = azd_value(f"MCP_{door.upper()}_CLIENT_ID")
    return _az_token(f"api://{client_id}/.default", f"the role of the {door} door")


def claims(bearer: str) -> dict:
    part = bearer.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class Door:
    """An MCP session with one door, as a context."""

    def __init__(self, door: str, bearer: str, mode: str = "auto"):
        self.url, self.mode = f"{base(door)}/mcp/{door}", mode
        self.http = httpx2.AsyncClient(headers={"authorization": f"Bearer {bearer}", **TEST_HEADER}, timeout=120)

    async def __aenter__(self) -> Client:
        self.client = Client(streamable_http_client(self.url, http_client=self.http), mode=self.mode)
        return await self.client.__aenter__()

    async def __aexit__(self, *exc) -> None:
        await self.client.__aexit__(*exc)
        await self.http.aclose()


async def call(c: Client, name: str, arguments: dict | None = None) -> tuple[bool, str]:
    result = await c.call_tool(name, arguments or {})
    return not result.is_error, "".join(x.text for x in result.content if isinstance(x, t.TextContent))


async def common(door: str, bearer: str, others: dict[str, str]) -> None:
    url = f"{base(door)}/mcp/{door}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.post(url, json=body, headers=HEADERS)
        challenge = r.headers.get("www-authenticate", "")
        check(f"{door}: no token is 401 and points to the sign-in", r.status_code == 401 and "resource_metadata=" in challenge)
        meta = (await http.get(challenge.split('resource_metadata="')[1].split('"')[0])).json()
        check(f"{door}: the metadata names the door", meta["resource"] == url, ", ".join(meta["scopes_supported"]))
        for name, other in others.items():
            r = await http.post(url, json=body, headers={**HEADERS, "authorization": f"Bearer {other}"})
            check(f"{door}: a token for {name} is refused", r.status_code == 401, str(r.status_code))
        mine = {**HEADERS, "authorization": f"Bearer {bearer}"}
        r = await http.post(url, json=body, headers={**mine, "origin": "https://evil.example"})
        check(f"{door}: a page in a browser is refused", r.status_code == 403)
        r = await http.post(url, content=b"x" * 70_000, headers=mine)
        check(f"{door}: a large body is refused", r.status_code == 413)
    for mode in ("auto", "legacy"):
        async with Door(door, bearer, mode) as c:
            names = {x.name for x in (await c.list_tools()).tools}
            check(f"{door}: tools with protocol {c.protocol_version}", EXPECTED.get(door, names) == names, ", ".join(sorted(names)))


async def operations(bearer: str) -> None:
    async with Door("operations", bearer) as c:
        for name, arguments in (("service_health", {"period": "7d"}), ("recent_turns", {}), ("what_people_ask", {}),
                                ("problem_turns", {"kind": "failed", "period": "7d"})):
            ok, text = await call(c, name, arguments)
            check(f"operations: {name}", ok and not any(p in text for p in PERSONAL), f"{len(text)} characters")
        ok, text = await call(c, "problem_turns", {"kind": "slow", "period": "7d"})
        turns = json.loads(text)["turns"] if ok else []
        check("operations: slow turns of 7 days", ok, f"{len(turns)} turns")
        if turns:
            ok, text = await call(c, "explain_turn", {"id": turns[0]["turn"]})
            steps = json.loads(text)["steps"] if ok else []
            check("operations: the slowest one explained", ok and bool(steps))
            for s in steps[:12]:
                note(f"         {s['service']}, {s['step']}, {s['duration_ms']} ms")
        ok, _ = await call(c, "explain_turn", {"id": 'x" or 1==1'})
        check("operations: an id that is a query is refused", not ok)


class PlayedCar:
    """A car page without the page: it holds a link, and answers commands as it is told to."""

    def __init__(self, on_command: Callable[[dict], Awaitable[dict | None]]):
        self.http = httpx.AsyncClient(base_url=api_url(), headers={"Authorization": f"Bearer {api_token()}"},
                                      timeout=httpx.Timeout(30, read=None))
        self.on_command, self.seen, self.ready = on_command, [], asyncio.Event()
        self.link = ""

    async def run(self) -> None:
        async with self.http.stream("POST", "/car/link", json={}) as r:
            r.raise_for_status()
            buffer: list[str] = []
            async for line in r.aiter_lines():
                buffer.append(line)
                if line:
                    continue
                for event, data in parse_sse(buffer):
                    if event == "link":
                        self.link = data["id"]
                        await self.state(True)
                        self.ready.set()
                    elif event == "command":
                        self.seen.append(data)
                        answer = await self.on_command(data)
                        if answer is not None:
                            (await self.http.post(f"/car/link/{self.link}/take", json={"id": data["id"]})).raise_for_status()
                            await self.http.post(f"/car/link/{self.link}/results", json={"id": data["id"], **answer})
                buffer = []

    async def state(self, parked: bool) -> None:
        r = await self.http.post(f"/car/link/{self.link}/state", json={"parked": parked, "odometer_km": 8214, "wipers": "off"})
        r.raise_for_status()


async def car(bearer: str) -> None:
    async with Door("car", bearer) as c:
        ok, text = await call(c, "honk", {"times": 1})
        check("car: no car is linked", not ok, text)
        answer: dict | None = {"ok": True}

        async def respond(command: dict) -> dict | None:
            return answer

        played = PlayedCar(respond)
        task = asyncio.create_task(played.run())
        try:
            await asyncio.wait_for(played.ready.wait(), 30)
            for name, arguments in (("honk", {"times": 2}), ("flash_lights", {"times": 3}), ("set_wipers", {"mode": "slow"})):
                ok, text = await call(c, name, arguments)
                check(f"car: {name}", ok, text)
            ok, text = await call(c, "car_status")
            check("car: status", ok, text)
            check("car: the car saw the app and only known commands",
                  [x["name"] for x in played.seen] == ["honk", "flash_lights", "set_wipers"] and {x["app"] for x in played.seen} == {"Azure CLI"})
            for name in ("set_speed", "park", "get_sign_photo"):
                ok, text = await call(c, name, {"kmh": 50})
                check(f"car: {name} does not exist", not ok, text)
            await played.state(False)
            ok, text = await call(c, "honk", {"times": 1})
            check("car: a moving car is refused", not ok and len(played.seen) == 3, text)
            await played.state(True)
            answer = None
            ok, text = await call(c, "honk", {"times": 1})
            check("car: a car that does not answer", not ok, text)
            for _ in range(8):
                ok, text = await call(c, "honk", {"times": 1})
                if not ok and "minute" in text:
                    break
            check("car: the limit per minute", not ok, text)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await played.http.aclose()


async def dealer(bearer: str) -> None:
    async with Door("dealer", bearer) as c:
        ok, before = await call(c, "my_stock")
        check("dealer: my stock", ok, before[:200])
        city = json.loads(before)["city"] if ok else ""
        more, text = await call(c, "change_stock", {"model": "spark", "change": 1, "city": "Nowhere"})
        check("dealer: one car more, in the own city whatever is said", more and f'"{city}"' in text, text[:120])
        if more:  # only then is there one to take back
            ok, text = await call(c, "change_stock", {"model": "spark", "change": -1})
            check("dealer: and back", ok, text[:120] if ok else f"THE STOCK OF SPARK IN {city} IS ONE TOO HIGH: {text[:80]}")
        ok, after = await call(c, "my_stock")
        check("dealer: the stock is as before", before == after)
        ok, text = await call(c, "change_stock", {"model": "spark", "change": 5})
        check("dealer: five at once is refused", not ok)
        ok, text = await call(c, "open_orders")
        check("dealer: open orders without name and address", ok and not any(w in text for w in ("address", "customer", "name\":")), text[:160])
        try:
            ok, text = await call(c, "deliver_order", {"number": "VO-999999"})
        except Exception as exc:  # a client that cannot ask
            ok, text = False, type(exc).__name__
        check("dealer: nothing is delivered by a client that cannot ask", not ok, text[:120])


async def customer(bearer: str) -> None:
    async with Door("customer", bearer) as c:
        for name, arguments, expected in (
            ("my_car", {}, "Spark"),
            ("search_documents", {"query": "towing capacity of the Fjord"}, "Fjord"),
            ("search_parts", {"query": "wiper blades for Voltrieve Spark"}, "VP-"),
            ("check_car_stock", {"model": "spark"}, "spark"),
            ("car_prices", {"market": "SE"}, ""),
            ("approved_answer", {"topic": "battery_emergency"}, "112"),
        ):
            ok, text = await call(c, name, arguments)
            check(f"customer: {name}", ok and expected.lower() in text.lower() and "sig=" not in text, f"{len(text)} characters")


async def runa(bearer: str, ask: bool) -> None:
    if not ask:
        return
    async with Door("runa", bearer) as c:
        ok, text = await call(c, "ask_runa", {"question": "Which wiper blades fit my car?"})
        check("runa: a question about my car", ok and "VP-" in text, text[:300].replace("\n", " "))
        ok, text = await call(c, "ask_runa", {"question": "I want to order a Fjord to Storgatan 1."})
        check("runa: no order is made", ok and "VO-" not in text, text[:300].replace("\n", " "))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doors", default="operations,car,dealer,customer,runa")
    parser.add_argument("--ask", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    doors = args.doors.split(",")
    tokens = {door: token(door) for door in doors}
    note(f"Signed in with the Azure CLI, roles {({d: claims(x).get('roles') for d, x in tokens.items()})}")
    for door in doors:
        others = {d: x for d, x in tokens.items() if d != door} | {"the API": api_token()}
        await common(door, tokens[door], others)
        match door:
            case "operations":
                await operations(tokens[door])
            case "car":
                await car(tokens[door])
            case "dealer":
                await dealer(tokens[door])
            case "customer":
                await customer(tokens[door])
            case "runa":
                await runa(tokens[door], args.ask)
    note(f"{sum(x.startswith(('ok', 'FAILED')) for x in lines)} checks, {failed} failed")
    if args.out:
        args.out.write_text("\n".join(lines) + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
