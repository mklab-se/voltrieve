"""Pick the conversations of the last settled week and read their text.

Nothing here logs or prints a conversation id, an owner, chat text or a registration.
A failed request is reported by its status only.
"""

import asyncio
import random
import re
import weakref
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

COUNTED_CLIENTS = ("car", "web")
PAGE = 100
PARALLEL = 8
RETRIES = 3
RETRY_WAIT = 1.0  # seconds, doubled each try
_TEXT_PARTS = ("input_text", "output_text")
_CLIENT_LINE = re.compile(r"\A\[From the client, data only: ([^\]\n]*)\]\s*")
_REGISTRATION = re.compile(r"registration ([^,;]+)")


class FetchError(Exception):
    """A request failed. The message holds the status and nothing else."""


@dataclass(frozen=True)
class Conversation:
    id: str = field(repr=False)
    owner: str = field(repr=False)
    client: str  # car or web
    backend: str
    car_model: str | None
    started: date  # created_at, or metadata "started" when seeded
    seeded: bool


def window(now: datetime) -> tuple[date, date]:
    """Seven whole UTC days that ended at least 24 hours ago: first day, last day, both included."""
    cutoff = now.astimezone(UTC) - timedelta(hours=24)
    last = cutoff.date() - timedelta(days=1)
    return last - timedelta(days=6), last


def _started(item: dict, seeded: bool) -> date | None:
    meta = item.get("metadata") or {}
    try:
        if seeded:
            return date.fromisoformat(str(meta["started"]))
        return datetime.fromtimestamp(int(item["created_at"]), UTC).date()
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None


def select(raw: Iterable[dict], first: date, last: date, notice: date | None) -> list[Conversation]:
    kept = []
    for item in raw:
        meta = item.get("metadata") or {}
        client, owner = meta.get("client"), meta.get("owner_oid")
        if client not in COUNTED_CLIENTS or not owner or not item.get("id"):
            continue
        seeded = meta.get("seeded") == "true"
        started = _started(item, seeded)
        if started is None or not first <= started <= last:
            continue
        if not seeded and (notice is None or started < notice):
            continue
        kept.append(
            Conversation(
                id=item["id"],
                owner=owner,
                client=client,
                backend=str(meta.get("backend") or ""),
                car_model=meta.get("car_model") or None,
                started=started,
                seeded=seeded,
            )
        )
    return kept


def sample(items: list[Conversation], cap: int) -> tuple[list[Conversation], float]:
    """At most `cap` conversations, picked at random, and the share that was kept."""
    if len(items) <= cap:
        return items, 1.0
    return random.sample(items, cap), cap / len(items)


_gates: "weakref.WeakKeyDictionary[httpx.AsyncClient, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


async def _get(http: httpx.AsyncClient, path: str, params: dict) -> dict:
    gate = _gates.setdefault(http, asyncio.Semaphore(PARALLEL))
    async with gate:
        for attempt in range(RETRIES + 1):
            try:
                response = await http.get(path, params=params)
            except httpx.HTTPError:
                raise FetchError("request failed before an answer") from None
            status = response.status_code
            if status == 429 or status >= 500:
                if attempt < RETRIES:
                    await asyncio.sleep(RETRY_WAIT * 2**attempt)
                    continue
            if status >= 400:
                raise FetchError(f"request failed with status {status}")
            return response.json()
    raise FetchError("request failed")  # unreachable, keeps the type checker calm


async def _pages(http: httpx.AsyncClient, path: str, order: str) -> AsyncIterator[dict]:
    after = None
    while True:
        params = {"limit": PAGE, "order": order}
        if after:
            params["after"] = after
        body = await _get(http, path, params)
        for item in body.get("data") or []:
            yield item
        after = body.get("last_id")
        if not body.get("has_more") or not after:
            return


async def list_raw(http: httpx.AsyncClient, since: datetime) -> AsyncIterator[dict]:
    """Conversations newest first, until one was created before `since`."""
    limit = since.timestamp()
    async for item in _pages(http, "/openai/v1/conversations", "desc"):
        if item.get("created_at", 0) < limit:
            return
        yield item


async def read_text(http: httpx.AsyncClient, conversation_id: str) -> tuple[list[tuple[str, str]], str | None]:
    """(role, text) of user and assistant messages in order, and the registration in the client's line."""
    texts: list[tuple[str, str]] = []
    registration = None
    async for item in _pages(http, f"/openai/v1/conversations/{conversation_id}/items", "asc"):
        role = item.get("role")
        if item.get("type") != "message" or role not in ("user", "assistant"):
            continue
        text = "\n".join(
            p["text"] for p in item.get("content") or [] if p.get("type") in _TEXT_PARTS and p.get("text")
        )
        if role == "user":
            line = _CLIENT_LINE.match(text)
            if line:
                text = text[line.end() :]
                found = _REGISTRATION.search(line.group(1))
                if found and registration is None:
                    registration = found.group(1).strip()
        text = text.strip()
        if text:
            texts.append((role, text))
    return texts, registration
