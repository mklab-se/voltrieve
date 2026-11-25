"""Limits in memory. They start again with the process, and belong to one replica."""

import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable

MAX_KEYS = 10_000


class RateLimit:
    """At most `calls` in `seconds`, for each key."""

    def __init__(self, calls: int, seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self._calls, self._seconds, self._clock = calls, seconds, clock
        self._seen: OrderedDict[object, deque[float]] = OrderedDict()

    def allow(self, key: object) -> bool:
        now = self._clock()
        times = self._seen.setdefault(key, deque())
        self._seen.move_to_end(key)
        while len(self._seen) > MAX_KEYS:
            self._seen.popitem(last=False)
        while times and now - times[0] >= self._seconds:
            times.popleft()
        if len(times) >= self._calls:
            return False
        times.append(now)
        return True


class Gate:
    """At most `size` calls at the same time, in all doors that share it, and `each` of one caller. No queue."""

    def __init__(self, size: int = 4, each: int = 2):
        self._size, self._each = size, each
        self._inside: Counter[object] = Counter()

    def enter(self, key: object) -> bool:
        if self._inside.total() >= self._size or self._inside[key] >= self._each:
            return False
        self._inside[key] += 1
        return True

    def leave(self, key: object) -> None:
        self._inside[key] -= 1
        if self._inside[key] <= 0:
            del self._inside[key]
