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
from urllib.parse import quote

import httpx

COUNTED_CLIENTS = ("car", "web")
PAGE = 100
PARALLEL = 8
RETRIES = 3
RETRY_WAIT = 1.0  # seconds, doubled each try
MAX_WAIT = 30.0  # longest Retry-After we honor, in seconds
ATTACHMENT_TEXT = "(attachment sent)"  # agent_core/src/voltrieve_agent/unstored.py, ends a photo turn's text
_TEXT_PARTS = ("input_text", "output_text", "text")
_CLIENT_LINE = re.compile(r"\A\s*\[From the client, data only: ([^\]\n]*)\]\s*")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    if now.tzinfo is None:
        raise ValueError("window needs a datetime with a time zone")
    cutoff = now.astimezone(UTC) - timedelta(hours=24)
    last = cutoff.date() - timedelta(days=1)
    return last - timedelta(days=6), last


def _started(item: dict, seeded: bool, meta: dict) -> date | None:
    try:
        if seeded:
            text = meta.get("started")
            if not isinstance(text, str) or not _DAY.fullmatch(text):
                return None
            return date.fromisoformat(text)
        created = item["created_at"]
        if isinstance(created, bool) or not isinstance(created, int):
            return None
        return datetime.fromtimestamp(created, UTC).date()
    except (KeyError, ValueError, OverflowError, OSError):
        return None


def select(raw: Iterable[dict], first: date, last: date, notice: date | None) -> list[Conversation]:
    kept = []
    for item in raw:
        meta = item.get("metadata")
        if not isinstance(meta, dict):
            continue
        client, owner, ident = meta.get("client"), meta.get("owner_oid"), item.get("id")
        if client not in COUNTED_CLIENTS or not isinstance(owner, str) or not owner:
            continue
        if not isinstance(ident, str) or not ident:
            continue
        seeded = meta.get("seeded") == "true"  # only this exact text, anything else is real
        started = _started(item, seeded, meta)
        if started is None or not first <= started <= last:
            continue
        if not seeded and (notice is None or started < notice):
            continue
        model = meta.get("car_model")
        backend = meta.get("backend")
        kept.append(
            Conversation(
                id=ident,
                owner=owner,
                client=client,
                backend=backend if isinstance(backend, str) else "",
                car_model=model if isinstance(model, str) and model else None,
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


def _wait(response: httpx.Response | None, attempt: int) -> float:
    """Retry-After in whole seconds if given (at most MAX_WAIT), else a doubling wait."""
    given = response.headers.get("retry-after", "") if response is not None else ""
    if given.isascii() and given.isdigit():
        return min(float(given), MAX_WAIT)
    return RETRY_WAIT * 2**attempt


async def _get(http: httpx.AsyncClient, path: str, params: dict) -> dict:
    """One page. Every failure is raised outside an except block, so no cause or context holds a URL or a body."""
    gate = _gates.setdefault(http, asyncio.Semaphore(PARALLEL))
    async with gate:
        status = None
        for attempt in range(RETRIES + 1):
            response = None
            try:
                response = await http.get(path, params=params)
            except httpx.HTTPError:
                status = None
            else:
                status = response.status_code
            if status is None or status == 429 or status >= 500:
                if attempt < RETRIES:
                    await asyncio.sleep(_wait(response, attempt))
                    continue
                break
            if status >= 400:
                break
            try:
                body = response.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise FetchError("the page could not be read")
            return body
    if status is None:
        raise FetchError("request failed before an answer")
    raise FetchError(f"request failed with status {status}")


async def _pages(http: httpx.AsyncClient, path: str, order: str) -> AsyncIterator[dict]:
    after = None
    seen: set[str] = set()
    while True:
        params = {"limit": PAGE, "order": order}
        if after:
            params["after"] = after
        body = await _get(http, path, params)
        for item in body["data"]:
            if not isinstance(item, dict):
                raise FetchError("the page could not be read")
            yield item
        if not body.get("has_more"):
            return
        after = body.get("last_id")
        if not isinstance(after, str) or not after or after in seen:
            raise FetchError("the page could not be paged")
        seen.add(after)


async def list_raw(http: httpx.AsyncClient, since: datetime) -> AsyncIterator[dict]:
    """Conversations newest first, until one was created before `since`."""
    if since.tzinfo is None:
        raise ValueError("list_raw needs a datetime with a time zone")
    limit = since.timestamp()
    async for item in _pages(http, "/openai/v1/conversations", "desc"):
        created = item.get("created_at")
        if isinstance(created, bool) or not isinstance(created, int):
            raise FetchError("the page could not be read")
        if created < limit:
            return
        yield item


def _registration(line: str) -> str | None:
    """The registration in the client's line. It may hold commas, so it ends at ', odometer ', ';' or the end."""
    ends = [i for i in (line.find(", odometer "), line.find(";")) if i >= 0]
    head = line[: min(ends)] if ends else line
    at = head.rfind(", registration ")
    if at >= 0:
        found = head[at + len(", registration ") :]
    elif head.startswith("registration "):
        found = head[len("registration ") :]
    else:
        return None
    return found.strip() or None


def _parts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        p["text"]
        for p in content
        if isinstance(p, dict) and p.get("type") in _TEXT_PARTS and isinstance(p.get("text"), str)
    ]


async def read_text(
    http: httpx.AsyncClient, conversation_id: str
) -> tuple[list[tuple[str, str]], tuple[str, ...]]:
    """(role, text) of user and assistant messages in order, and every registration in the client's lines.

    A conversation with no user text gives no user message here. The caller drops it.
    """
    messages: list[tuple[str, str]] = []
    registrations: list[str] = []
    path = f"/openai/v1/conversations/{quote(conversation_id, safe='')}/items"
    async for item in _pages(http, path, "asc"):
        role = item.get("role")
        if item.get("type") != "message" or role not in ("user", "assistant"):
            continue
        kept = []
        for text in _parts(item.get("content")):
            if role == "user":
                while (line := _CLIENT_LINE.match(text)) is not None:
                    found = _registration(line.group(1))
                    if found and found not in registrations:
                        registrations.append(found)
                    text = text[line.end() :]
            kept.append(text)
        text = "\n".join(kept).strip()
        if role == "user":
            text = text.removesuffix(ATTACHMENT_TEXT).strip()
        if text:
            messages.append((role, text))
    return messages, tuple(registrations)
