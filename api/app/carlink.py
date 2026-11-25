"""The cars that take commands from their owner's AI. A car page with remote control on holds one stream
here, and a command goes to the link that its owner opened last.

Everything is in memory and belongs to one replica. Nothing is kept for a car that is not linked, and
nothing about the car or its owner is logged.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send
from voltrieve_agent.events import KEEPALIVE, AgentEvent, format_event
from voltrieve_doors import RateLimit

from app.auth import Claims
from app.clients import AZURE_CLI_CLIENT_ID
from app.routes import SSE_HEADERS, authenticate, json_body
from app.schemas import ID_PATTERN

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 10.0
LINK_SECONDS = 30 * 60.0
ANSWER_SECONDS = 5.0
LINKS_PER_PERSON = 3
MAX_LINKS = 50
COMMANDS_PER_MINUTE = 6

Wipers = Literal["off", "slow", "fast"]
CarCode = Literal["off", "moving", "unknown", "bad_arguments", "no_sound", "failed"]


class CarState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    parked: bool
    odometer_km: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    wipers: Wipers | None = None


class CarResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=ID_PATTERN)
    ok: bool
    message: CarCode | None = None


@dataclass(eq=False)
class Link:
    oid: str = field(repr=False)
    until: float
    id: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    parked: bool = False  # until the car says that it is
    odometer_km: int | None = field(default=None, repr=False)
    wipers: Wipers | None = field(default=None, repr=False)
    closed: str = ""  # why
    queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)
    waiting: dict[str, asyncio.Future] = field(default_factory=dict, repr=False)


class CarLinks:
    def __init__(self, clock: Callable[[], float] = time.monotonic, answer_seconds: float = ANSWER_SECONDS):
        self._clock, self._answer_seconds = clock, answer_seconds
        self._links: dict[str, Link] = {}  # the oldest first
        self._rate = RateLimit(COMMANDS_PER_MINUTE, clock=clock)

    def open(self, oid: str, seconds: float) -> Link:
        mine = [link for link in self._links.values() if link.oid == oid]
        if len(mine) >= LINKS_PER_PERSON:
            self.close(mine[0], "replaced")
        if len(self._links) >= MAX_LINKS:
            self.close(next(iter(self._links.values())), "replaced")
        link = Link(oid, self._clock() + seconds)
        self._links[link.id] = link
        logger.info("Car link opened: %d open", len(self._links))
        return link

    def close(self, link: Link, why: str = "expired") -> None:
        if self._links.pop(link.id, None) is None:
            return
        link.closed = why
        link.queue.put_nowait(None)  # the stream ends
        for future in link.waiting.values():
            if not future.done():
                future.set_result("gone")
        logger.info("Car link closed: %d open", len(self._links))

    def get(self, oid: str, link_id: str) -> Link | None:
        link = self._links.get(link_id)
        return link if link and link.oid == oid and not self._expired(link) else None

    def latest(self, oid: str) -> Link | None:
        """The link this person opened last. There is no other way to name a car."""
        mine = [link for link in self._links.values() if link.oid == oid and not self._expired(link)]
        return mine[-1] if mine else None

    def _expired(self, link: Link) -> bool:
        return self._clock() >= link.until

    def state(self, link: Link, state: CarState) -> None:
        link.parked = state.parked
        if state.odometer_km is not None:
            link.odometer_km = state.odometer_km
        if state.wipers is not None:
            link.wipers = state.wipers

    def answer(self, link: Link, result: CarResult) -> None:
        """What the car did. One that is late, repeated or never asked for is ignored."""
        future = link.waiting.get(result.id)
        if future and not future.done():
            future.set_result("done" if result.ok else result.message or "failed")

    async def command(self, oid: str, name: str, arguments: dict, app: str) -> str:
        """Ask the car, and return a code: "done", or why not."""
        link = self.latest(oid)
        if link is None:
            return "not_linked"
        if not link.parked:
            return "moving"
        if not self._rate.allow(oid):
            return "too_many"
        command_id = secrets.token_urlsafe(16)
        future = asyncio.get_running_loop().create_future()
        link.waiting[command_id] = future
        link.queue.put_nowait({"id": command_id, "name": name, "arguments": arguments, "app": app})
        try:
            return await asyncio.wait_for(future, self._answer_seconds)
        except TimeoutError:
            return "no_answer"
        finally:
            link.waiting.pop(command_id, None)

    async def events(self, link: Link) -> AsyncIterator[bytes]:
        yield format_event(AgentEvent("link", {"id": link.id}))
        while not link.closed:
            left = link.until - self._clock()
            if left <= 0:
                self.close(link)
                break
            try:
                command = await asyncio.wait_for(link.queue.get(), min(KEEPALIVE_SECONDS, left))
            except TimeoutError:
                yield KEEPALIVE
                continue
            # A command that nobody waits for any more is not done late.
            if command and command["id"] in link.waiting:
                yield format_event(AgentEvent("command", command))
        yield format_event(AgentEvent("end", {"reason": link.closed}))


class LinkResponse(StreamingResponse):
    """The link is there for as long as the response is, however it ends."""

    def __init__(self, links: CarLinks, oid: str, seconds: float):
        super().__init__(iter(()), media_type="text/event-stream", headers=SSE_HEADERS)
        self._open = lambda: links.open(oid, seconds)
        self._links = links

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        link = self._open()
        self.body_iterator = self._links.events(link)
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._links.close(link, "gone")


async def _car(request: Request, claims: Claims = Depends(authenticate)) -> Claims:
    """The car, or with MCP_ALLOW_CLI the Azure CLI, so that a script can play it."""
    settings = request.app.state.settings
    if not (settings.mcp_api_url and settings.mcp_car_client_id):
        raise HTTPException(404, "There is no door to the car")
    allowed = {settings.car_client_id} - {""}
    if settings.mcp_allow_cli:
        allowed.add(AZURE_CLI_CLIENT_ID)
    if claims.azp not in allowed:
        raise HTTPException(403, "Only the car may do this")
    return claims


def _link(request: Request, link: str, claims: Claims = Depends(_car)) -> Link:
    """An unknown, closed or someone else's link gives the same answer, so ids cannot be probed."""
    found = request.app.state.car_links.get(claims.oid, link)
    if found is None:
        raise HTTPException(404, "Unknown car link")
    return found


def _seconds_left(authorization: str) -> float:
    """Of the token, which authenticate has checked."""
    payload = jwt.decode(authorization.split(" ")[1], options={"verify_signature": False})
    return float(payload["exp"]) - time.time()


def build_car_router() -> APIRouter:
    router = APIRouter(prefix="/car/link")

    @router.post("")
    async def open_link(request: Request, claims: Claims = Depends(_car)) -> Response:
        seconds = min(LINK_SECONDS, _seconds_left(request.headers["authorization"]))
        return LinkResponse(request.app.state.car_links, claims.oid, seconds)

    @router.post("/{link}/state", status_code=204)
    async def state(
        request: Request, link: Link = Depends(_link), body: CarState = Depends(json_body(CarState))
    ) -> None:
        request.app.state.car_links.state(link, body)

    @router.post("/{link}/results", status_code=204)
    async def results(
        request: Request, link: Link = Depends(_link), body: CarResult = Depends(json_body(CarResult))
    ) -> None:
        request.app.state.car_links.answer(link, body)

    return router
