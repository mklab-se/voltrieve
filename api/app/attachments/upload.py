"""Reading an upload and processing it. Nothing of it is written to disk or logged.

The usual form parser keeps files over 1 MB in a temporary file and reads the whole body before the
caller sees it. This one keeps the bytes in memory and stops at the limit of the kind, which it finds
from the first bytes.
"""

import asyncio
from collections import Counter
from collections.abc import Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from typing import NoReturn

from fastapi import HTTPException, Request
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import ClientDisconnect

from app import attachments
from app.text import one_line

from .base import AttachmentHandler, Part

MAX_LABEL = 80  # characters
MAX_LABEL_BYTES = MAX_LABEL * 4
HEAD_BYTES = 512  # what a handler sees before it accepts a file
MAX_PER_USER = 2  # uploads at a time
MAX_ARRIVING = 16  # in all, with the body on its way
MAX_WAITING = 8  # in all, for the worker
READ_SECONDS = 120.0  # for the whole body
IDLE_SECONDS = 20.0  # for the next piece of it
INTERRUPTED = "The upload was interrupted"


def refuse(status: int, detail: str | dict) -> NoReturn:
    raise HTTPException(status, detail)


def refuse_busy() -> NoReturn:
    refuse(503, {"code": "busy"})


@dataclass
class Upload:
    handler: AttachmentHandler
    data: bytes = field(repr=False)
    label: str = field(repr=False)


class _Form:
    """The two parts of the form, `file` and `label`, filled by the parser's callbacks."""

    def __init__(self) -> None:
        self.parts: dict[str, bytearray] = {}
        self.handler: AttachmentHandler | None = None
        self.ended = False
        self._header = self._value = self._disposition = b""
        self._name = ""

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
            "on_end": self._end,
        }

    def _part_begin(self) -> None:
        self._disposition = b""

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._header += data[start:end]

    def _header_value(self, data: bytes, start: int, end: int) -> None:
        self._value += data[start:end]

    def _header_end(self) -> None:
        if self._header.lower() == b"content-disposition":
            self._disposition = self._value
        self._header = self._value = b""

    def _headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        self._name = options.get(b"name", b"").decode("latin-1")
        if self._name not in ("file", "label") or self._name in self.parts:
            refuse(422, "Send one file and at most one label")
        self.parts[self._name] = bytearray()

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        part, chunk = self.parts[self._name], data[start:end]
        if self._name == "label":
            if len(part) + len(chunk) > MAX_LABEL_BYTES:
                refuse(422, "The label is too long")
        elif self.handler is None:
            needed = HEAD_BYTES - len(part)
            part += chunk[:needed]
            chunk = chunk[needed:]
            if len(part) < HEAD_BYTES:
                return
            self._identify()
        if self.handler is not None and self._name == "file":
            self._check_size(len(part) + len(chunk))
        part += chunk

    def _part_end(self) -> None:
        if self._name == "file" and self.handler is None:
            self._identify()
            self._check_size(len(self.parts["file"]))

    def _end(self) -> None:
        self.ended = True

    def _identify(self) -> None:
        # Looked up here, so a new kind in HANDLERS is all it takes.
        self.handler = attachments.identify(bytes(self.parts["file"]), attachments.HANDLERS)
        if self.handler is None:
            refuse(415, "Unknown kind of attachment")

    def _check_size(self, size: int) -> None:
        if size > self.handler.max_bytes:
            refuse(413, "The attachment is too large")


async def read_upload(request: Request) -> Upload:
    content_type, options = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data" or not options.get(b"boundary"):
        refuse(422, "Send the attachment as multipart/form-data")
    form = _Form()
    try:
        parser = MultipartParser(options[b"boundary"], form.callbacks())
        async with asyncio.timeout(READ_SECONDS), aclosing(request.stream()) as chunks:
            while True:
                async with asyncio.timeout(IDLE_SECONDS):
                    chunk = await anext(chunks, None)
                if chunk is None:
                    break
                parser.write(chunk)
    except FormParserError:
        refuse(422, "Invalid multipart data")
    except TimeoutError:
        refuse(408, "The upload took too long")
    except ClientDisconnect:
        refuse(400, INTERRUPTED)  # nobody is there to read it
    if not form.ended:
        refuse(422, "Invalid multipart data")
    if form.handler is None:
        refuse(422, "The file is missing")
    try:
        label = form.parts.get("label", b"").decode()
    except UnicodeDecodeError:
        refuse(422, "The label is not UTF-8")
    if len(label) > MAX_LABEL:
        refuse(422, "The label is too long")
    return Upload(form.handler, bytes(form.parts["file"]), one_line(label) or form.handler.kind)


class UploadQueue:
    """The uploads in hand. Each has its body in memory, so their number is bounded: per user, so that
    nobody can keep the others out, while the body arrives, and while it waits for the worker, which
    takes one at a time because a decoded photo is large."""

    def __init__(self) -> None:
        self.places: Counter[str] = Counter()  # by user
        self.arriving = 0
        self.waiting = 0
        self._worker = asyncio.Semaphore(1)

    @contextmanager
    def place(self, oid: str) -> Iterator[None]:
        """A user's place, from the arrival until the attachment is processed."""
        if self.places[oid] >= MAX_PER_USER:
            refuse_busy()
        self.places[oid] += 1
        try:
            yield
        finally:
            self.places[oid] -= 1
            if not self.places[oid]:
                del self.places[oid]

    async def read(self, request: Request) -> Upload:
        if self.arriving >= MAX_ARRIVING:
            refuse_busy()
        self.arriving += 1
        try:
            return await read_upload(request)
        finally:
            self.arriving -= 1

    async def process(self, request: Request, upload: Upload) -> list[Part]:
        if self._worker.locked() and self.waiting >= MAX_WAITING:
            refuse_busy()
        self.waiting += 1
        try:
            await self._worker.acquire()
        finally:
            self.waiting -= 1
        try:
            if await request.is_disconnected():
                refuse(400, INTERRUPTED)
            return await asyncio.to_thread(upload.handler.process, upload.data, upload.label)
        finally:
            self._worker.release()
