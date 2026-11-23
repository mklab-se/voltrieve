"""The types every attachment handler shares."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Part:
    label: str
    content: dict
    size: int  # bytes
    detail: str = ""  # for a photo, like "1200x1600"


class Rejected(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class AttachmentHandler(Protocol):
    kind: str
    max_bytes: int

    def accepts(self, head: bytes) -> bool: ...

    def process(self, data: bytes, label: str) -> list[Part]: ...
