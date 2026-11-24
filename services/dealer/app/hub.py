"""The log of the calls, and the open pages that follow it. It lives in memory, in the one event loop.

An entry names a model, a city, an order and staff. It never holds an address, or anything a caller typed.
"""

import asyncio
import itertools
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

LOG_SIZE = 200
QUEUE_SIZE = 100

Item = tuple[str, dict]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Hub:
    def __init__(self, clock: Callable[[], datetime] = utc_now):
        self._clock = clock
        self._ids = itertools.count(1)
        self._log: deque[dict] = deque(maxlen=LOG_SIZE)
        self._queues: set[asyncio.Queue[Item]] = set()

    def add(
        self,
        kind: str,
        who: str | None,
        *,
        client: str | None = None,
        model: str | None = None,
        city: str | None = None,
        number: str | None = None,
        result: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """`result` is a short fixed text or a count."""
        entry = {
            "id": next(self._ids),
            "at": self._clock().isoformat(),
            "kind": kind,
            "who": who,
            "client": client,
            "model": model,
            "city": city,
            "number": number,
            "result": result,
            "correlation_id": correlation_id,
        }
        self._log.append(entry)
        self._send("log", entry)

    def latest(self) -> list[dict]:
        """The oldest first."""
        return list(self._log)

    def changed(self) -> None:
        """The stock or the orders changed."""
        self._send("changed", {})

    def clear(self) -> None:
        self._log.clear()
        self._send("clear", {})

    def subscribe(self) -> asyncio.Queue[Item]:
        queue: asyncio.Queue[Item] = asyncio.Queue(QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Item]) -> None:
        self._queues.discard(queue)

    def subscribed(self, queue: asyncio.Queue[Item]) -> bool:
        return queue in self._queues

    def _send(self, name: str, data: dict) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait((name, data))
            except asyncio.QueueFull:  # too slow: it has to connect again
                self._queues.discard(queue)
