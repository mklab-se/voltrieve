"""Attachments between the upload and the turn that names them, in this process's memory.

The API runs with one replica, so this needs no other store. A restart loses them, and the client
uploads again.
"""

import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .base import Part

TTL_SECONDS = 15 * 60
MAX_PER_USER = 10
MAX_TOTAL_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Stored:
    id: str = field(repr=False)
    kind: str
    parts: list[Part] = field(repr=False)
    expires_at: float  # on the store's own clock
    oid: str = field(repr=False)
    # What the content takes in memory. A photo is held as base64, a third more than the JPEG.
    memory: int

    @property
    def size(self) -> int:
        """The size of the processed file, for the trace."""
        return sum(p.size for p in self.parts)

    @property
    def detail(self) -> str:
        return ", ".join(p.detail for p in self.parts if p.detail)

    def summary(self) -> dict:
        """What a trace may say about it."""
        return {"kind": self.kind, "detail": self.detail, "size": self.size}


class AttachmentStore:
    def __init__(
        self,
        ttl: float = TTL_SECONDS,
        per_user: int = MAX_PER_USER,
        max_bytes: int = MAX_TOTAL_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl = ttl
        self._per_user = per_user
        self._max_bytes = max_bytes
        self._clock = clock
        self._items: dict[str, Stored] = {}  # oldest first

    def __len__(self) -> int:
        return len(self._items)

    @property
    def memory(self) -> int:
        return sum(s.memory for s in self._items.values())

    def put(self, oid: str, kind: str, parts: list[Part]) -> Stored:
        """Keep an attachment. At a limit the oldest goes, the user's own first."""
        memory = sum(len(json.dumps(p.content)) for p in parts)
        stored = Stored(secrets.token_urlsafe(24), kind, parts, self._clock() + self.ttl, oid, memory)
        if memory > self._max_bytes:
            raise ValueError("the attachment is larger than the store")
        self.evict_expired()
        while sum(s.oid == oid for s in self._items.values()) >= self._per_user:
            self._drop_oldest(oid)
        while self.memory + memory > self._max_bytes:
            self._drop_oldest(oid)
        self._items[stored.id] = stored
        return stored

    def get(self, oid: str, id: str) -> Stored | None:
        """The user's own live attachment. Why there is none is never told."""
        stored = self._items.get(id)
        if stored is None or stored.oid != oid or self._clock() >= stored.expires_at:
            return None
        return stored

    def evict_expired(self) -> int:
        now = self._clock()
        expired = [s.id for s in self._items.values() if now >= s.expires_at]
        for id in expired:
            del self._items[id]
        return len(expired)

    def _drop_oldest(self, oid: str) -> None:
        own = (s.id for s in self._items.values() if s.oid == oid)
        del self._items[next(own, None) or next(iter(self._items))]


def attachment_parts(stored: list[Stored]) -> list[dict]:
    """The content parts for the model, each after a text that says which attachment it is."""
    return [
        content
        for number, attachment in enumerate(stored, 1)
        for part in attachment.parts
        for content in (
            {"type": "input_text", "text": f"Attachment {number} ({attachment.kind}): {part.label}"},
            part.content,
        )
    ]
