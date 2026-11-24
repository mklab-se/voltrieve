"""What every request goes through, before FastAPI reads anything of it."""

import logging
import re
import time
import traceback
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_setup import correlation_id
from app.model import one_line
from app.telemetry import annotate_span

logger = logging.getLogger("app.access")
CORRELATION_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
MAX_BODY_BYTES = 64 * 1024
LOGGED_PATH = 200  # characters


class BodyTooLarge(HTTPException):
    """An HTTPException, so FastAPI passes it through while reading the body (any other
    exception there becomes a 400) and turns it into a 413 response."""

    def __init__(self) -> None:
        super().__init__(413, "Request body too large")


def too_large(content_length: str) -> bool:
    if not (content_length.isascii() and content_length.isdigit()):
        return False  # the bytes are counted too
    return len(content_length) > 9 or int(content_length) > MAX_BODY_BYTES


def where(exc: Exception) -> str:
    """The type and the place of an error. Its text can hold what a caller sent."""
    last = traceback.extract_tb(exc.__traceback__)[-1]
    return f"{type(exc).__name__} at {Path(last.filename).name}:{last.lineno}"


class RequestGuard:
    """Pure ASGI middleware: correlation id, body limit, access log, and errors that nobody expected.

    FastAPI reads the body before it checks the token, so the limit is for everyone.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        supplied = headers.get("x-correlation-id", "")
        cid = supplied if CORRELATION_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        token = correlation_id.set(cid)
        annotate_span(correlation_id=cid)
        start = time.perf_counter()
        status, started, received = 500, False, 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                status, started = message["status"], True
                added = [(b"x-correlation-id", cid.encode())]
                if scope["path"].startswith("/staff/"):  # names and addresses
                    added.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message["headers"], *added]}
            await send(message)

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise BodyTooLarge
            return message

        error, unexpected = None, None
        try:
            try:
                if too_large(headers.get("content-length", "")):
                    raise BodyTooLarge
                await self.app(scope, receive_wrapper, send_wrapper)
            except BodyTooLarge:
                error = JSONResponse({"detail": "Request body too large"}, 413)
            except Exception as exc:
                unexpected = where(exc)
                error = JSONResponse({"detail": "Internal error"}, 500)
            if unexpected:
                logger.error("Unhandled error: %s", unexpected)
            if error and started:  # outside the except block, so nothing is chained
                raise RuntimeError("the answer was cut off")
            if error:
                await error(scope, receive, send_wrapper)
        finally:  # also when the request is cancelled, as a stream is at a stop
            logger.info(
                "request",
                extra={
                    "path": one_line(scope["path"][:LOGGED_PATH]),
                    "status": status,
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            correlation_id.reset(token)
