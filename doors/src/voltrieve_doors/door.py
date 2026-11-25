"""A door: one MCP endpoint for one audience, with its own tokens, tools and rules.

In front of the MCP SDK stands a guard. It answers what must never reach the SDK, and it keeps what a
caller sends out of logs and spans. The SDK's own tracing and logging are off: they quote tool names,
request ids and error texts (docs/superpowers/specs/2026-11-25-mcp-doors-design.md, section 4).
"""

import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server._otel import OpenTelemetryMiddleware
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from voltrieve_doors.limits import Gate, RateLimit
from voltrieve_doors.token import AuthError, Caller, DoorTokenValidator

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("voltrieve.doors")

MAX_BODY_BYTES = 64 * 1024
MAX_DEPTH = 32
CALLS_PER_MINUTE = 30
REQUESTS_PER_CALL = 5  # what an older client sends to connect, beside the call itself
WELL_KNOWN = "/.well-known/oauth-protected-resource"
NO_STORE = (b"cache-control", b"no-store")
FAILED = "That did not work. Try again later."
NOT_ALLOWED = "You may not use this tool."
TOO_LARGE = "The answer is too large. Ask for less."
MAX_ANSWER_CHARS = 60_000

# The SDK logs the texts of errors, and what a caller calls a tool.
logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)

_caller: ContextVar[Caller] = ContextVar("door_caller")
_headers: ContextVar[dict[str, str]] = ContextVar("door_headers")


def caller() -> Caller:
    """Who calls, inside a tool."""
    return _caller.get()


def request_header(name: str) -> str:
    """A header of the request, inside a tool. It is the caller's own text."""
    return _headers.get({}).get(name.lower(), "")


class Refused(Exception):
    """What a tool says when it will not do it. The text is ours, and goes to the caller."""


@dataclass(frozen=True)
class DoorTool:
    """`fn` is an async function whose parameters become the schema. The rules are checked on every call."""

    name: str
    title: str
    description: str
    fn: Callable[..., Awaitable[object]]
    roles: frozenset[str]  # any of them
    scope: str  # a person's token must have it
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    people_only: bool = False

    def allows(self, who: Caller) -> bool:
        if who.application and self.people_only:
            return False
        return bool(self.roles & who.roles) and (who.application or self.scope in who.scopes)


@dataclass(frozen=True)
class Call:
    """What is kept of a call. Every text in it is our own."""

    door: str
    tool: str
    app: str
    result: str  # done, refused, failed, denied or asked
    elapsed_ms: int
    caller: Caller = field(repr=False)


@dataclass(frozen=True)
class DoorConfig:
    name: str  # operations, car, dealer or customer
    url: str  # https://host/mcp/name, as callers see it
    tenant_id: str
    client_id: str
    identifier: str  # the Application ID URI: the url, or api://<client id>
    scopes: tuple[str, ...]
    apps: dict[str, str]  # client id to the name we show
    applications: bool = False
    issuer: str = ""  # another authorization server to point to, for the preview of client ID metadata documents

    @property
    def authorization_server(self) -> str:
        return self.issuer or f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


def _denied(door: str, reason: str, **extra: str) -> None:
    logger.warning("authorization_denied", extra={"door": door, "reason": reason, **extra})


class Door:
    def __init__(
        self,
        config: DoorConfig,
        tools: Sequence[DoorTool],
        instructions: str,
        *,
        gate: Gate | None = None,
        validator: DoorTokenValidator | None = None,
        rate: RateLimit | None = None,
        on_call: Callable[[Call], None] | None = None,
        correlation: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self.config = config
        parsed = urlparse(config.url)
        self.path, self._host = parsed.path, parsed.netloc.lower()
        if not self.path.startswith("/mcp/") or self.path.endswith("/") or not self._host:
            raise ValueError(f"not an address of a door: {config.url}")
        self.metadata_path = WELL_KNOWN + self.path
        self._metadata_url = f"{parsed.scheme}://{parsed.netloc}{self.metadata_path}"
        self._tools = {t.name: t for t in tools}
        self._gate = gate or Gate()
        self._rate = rate or RateLimit(CALLS_PER_MINUTE)
        self._requests = RateLimit(CALLS_PER_MINUTE * REQUESTS_PER_CALL)
        self._on_call, self._clock, self._correlation = on_call, clock, correlation
        self._validator = validator or DoorTokenValidator(
            config.tenant_id,
            config.client_id,
            config.identifier,
            frozenset(config.scopes),
            config.apps,
            applications=config.applications,
        )
        self._mcp = self._server(instructions)
        self._app = self._mcp.streamable_http_app(
            streamable_http_path=self.path,
            json_response=True,
            stateless_http=True,
            max_request_body_size=MAX_BODY_BYTES,
            # The guard checks the host and the origin, before the token.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    # ---- the MCP server

    def _server(self, instructions: str) -> MCPServer:
        server = MCPServer(f"voltrieve-{self.config.name}", instructions=instructions, version="1.0.0")
        server.middleware[:] = [m for m in server.middleware if not isinstance(m, OpenTelemetryMiddleware)]
        server.middleware.append(self._rules)
        for tool in self._tools.values():
            server.tool(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                annotations=ToolAnnotations(
                    read_only_hint=tool.read_only,
                    destructive_hint=tool.destructive,
                    idempotent_hint=tool.idempotent,
                    open_world_hint=False,
                ),
                structured_output=False,
            )(_quiet(tool.fn))
        return server

    async def _rules(self, ctx, call_next):
        if ctx.method == "tools/list":
            result = await call_next(ctx)
            return _only(result, {t.name for t in self._tools.values() if t.allows(caller())})
        if ctx.method != "tools/call":
            return await call_next(ctx)
        tool = self._tools.get(str((ctx.params or {}).get("name")))
        if tool is None:
            return await call_next(ctx)  # the SDK says that it is unknown
        who, start = caller(), self._clock()
        with tracer.start_as_current_span(
            "door.call", record_exception=False, set_status_on_exception=False
        ) as span:
            outcome = "failed"
            try:
                if not tool.allows(who):  # a tool that is not listed can be called all the same
                    outcome = "denied"
                    _denied(self.config.name, "role_or_scope", tool=tool.name, app=who.app)
                    return _error(NOT_ALLOWED)
                result = await call_next(ctx)
                outcome = _outcome(result)
                if outcome == "done" and len(_wire(result)) > MAX_ANSWER_CHARS:
                    outcome = "refused"
                    return _error(TOO_LARGE)
                return result
            except MCPError:
                outcome = "refused"  # such as a client that cannot ask the person
                raise
            finally:
                self._keep(Call(self.config.name, tool.name, who.app, outcome,
                                round((self._clock() - start) * 1000), who), span)

    def _keep(self, call: Call, span: trace.Span) -> None:
        values = {"door": call.door, "tool": call.tool, "app": call.app, "result": call.result}
        for key, value in {**values, "elapsed_ms": call.elapsed_ms}.items():
            span.set_attribute(f"voltrieve.door.{key}", value)
        if cid := self._correlation():
            span.set_attribute("correlation_id", cid)
        logger.info("door_call", extra={**values, "elapsed_ms": call.elapsed_ms})
        if self._on_call:
            self._on_call(call)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        async with self._mcp.session_manager.run():
            yield

    # ---- the guard

    def takes(self, path: str) -> bool:
        return path in (self.path, self.metadata_path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if headers.get("host", "").lower() != self._host:
            return await _reply(send, 421, {"error": "wrong_host"})
        if scope["path"] == self.metadata_path:
            if scope["method"] not in ("GET", "HEAD"):
                return await _reply(send, 405, {"error": "method_not_allowed"}, [(b"allow", b"GET, HEAD")])
            return await _reply(send, 200, self._metadata(), [(b"access-control-allow-origin", b"*")])
        if "origin" in headers:  # no page in a browser calls a door
            return await _reply(send, 403, {"error": "origin_not_allowed"})
        if scope["method"] != "POST":
            return await _reply(send, 405, {"error": "method_not_allowed"}, [(b"allow", b"POST")])
        try:
            who = await self._validator.validate(headers.get("authorization"))
        except AuthError as exc:
            if headers.get("authorization"):
                _denied(self.config.name, exc.detail.lower().replace(" ", "_"))
            return await _reply(send, exc.status, {"error": exc.error, "error_description": exc.detail},
                                self._challenge(exc))
        body = await _read(receive, headers.get("content-length", ""))
        if body is None:
            return await _reply(send, 413, {"error": "too_large"})
        message = _message(body)
        if message is None:
            return await _reply(send, 400, _rpc_error(-32600, "Send one JSON-RPC message."))
        calls = message.get("method") == "tools/call"
        who_and_app = (who.oid, who.app)
        if not self._requests.allow(who_and_app) or (calls and not self._rate.allow(who_and_app)):
            return await _reply(send, 429, {"error": "too_many_requests"}, [(b"retry-after", b"60")])
        if calls and not self._gate.enter():
            return await _reply(send, 429, {"error": "busy"}, [(b"retry-after", b"2")])
        tokens = _caller.set(who), _headers.set(headers)
        try:
            await self._app(scope, _replay(body), _silent(send))
        finally:
            _caller.reset(tokens[0])
            _headers.reset(tokens[1])
            if calls:
                self._gate.leave()

    def _metadata(self) -> dict:
        return {
            "resource": self.config.url,
            "authorization_servers": [self.config.authorization_server],
            "scopes_supported": self._scopes(),
            "bearer_methods_supported": ["header"],
            "resource_name": f"Voltrieve {self.config.name}",
        }

    def _scopes(self) -> list[str]:
        return [f"{self.config.identifier}/{scope}" for scope in self.config.scopes]

    def _challenge(self, exc: AuthError) -> list[tuple[bytes, bytes]]:
        if exc.status == 503:
            return [(b"retry-after", b"5")]
        parts = [f'resource_metadata="{self._metadata_url}"', f'scope="{" ".join(self._scopes())}"']
        if exc.status == 403 and exc.error != "insufficient_scope":
            return []
        if exc.detail != "Missing bearer token":
            parts.insert(0, f'error="{exc.error}"')
        return [(b"www-authenticate", ("Bearer " + ", ".join(parts)).encode())]


class Doors:
    """ASGI middleware: takes the paths of the doors, and leaves every other request alone."""

    def __init__(self, app: ASGIApp, doors: Iterable[Door]):
        self._app, self._doors = app, list(doors)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            for door in self._doors:
                if door.takes(scope["path"]):
                    return await door(scope, receive, send)
        await self._app(scope, receive, send)


@asynccontextmanager
async def running(doors: Iterable[Door]) -> AsyncIterator[None]:
    """For the lifespan of the app."""
    async with AsyncExitStack() as stack:
        for door in doors:
            await stack.enter_async_context(door.run())
        yield


def _quiet(fn: Callable[..., Awaitable[object]]):
    """The same function to the SDK, whose errors say only what we chose."""
    import functools

    @functools.wraps(fn)
    async def run(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Refused as exc:
            raise ToolError(str(exc)) from None
        except (ToolError, MCPError):
            raise
        except Exception as exc:
            logger.warning("A tool of a door failed: %s", type(exc).__name__)
            raise _Broken(FAILED) from None

    return run


class _Broken(ToolError):
    pass


def _error(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _wire(result: object) -> str:
    dumped = result if isinstance(result, dict) else result.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    return json.dumps(dumped, default=str)


def _outcome(result: object) -> str:
    dumped = result if isinstance(result, dict) else result.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
    if dumped.get("resultType") == "input_required":
        return "asked"
    if not dumped.get("isError"):
        return "done"
    texts = [c.get("text", "") for c in dumped.get("content", [])]
    return "failed" if any(FAILED in text for text in texts) else "refused"


def _only(result, names: set[str]):
    if isinstance(result, dict):
        return {**result, "tools": [t for t in result.get("tools", []) if _tool_name(t) in names]}
    return result.model_copy(update={"tools": [t for t in result.tools if _tool_name(t) in names]})


def _tool_name(tool) -> str:
    return tool["name"] if isinstance(tool, dict) else tool.name


def _rpc_error(code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}


def _message(body: bytes) -> dict | None:
    """The one JSON-RPC message, or None. Not deeper than anything we need."""
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        return None
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return None
        if isinstance(node, dict | list):
            stack.extend((c, depth + 1) for c in (node.values() if isinstance(node, dict) else node))
    return value


async def _read(receive: Receive, length: str) -> bytes | None:
    if length.isascii() and length.isdigit() and (len(length) > 9 or int(length) > MAX_BODY_BYTES):
        return None
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return body
        body += message.get("body", b"")
        if len(body) > MAX_BODY_BYTES:
            return None
        if not message.get("more_body"):
            return body


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            # The SDK waits for a disconnect only while it answers; the answer is one piece.
            import asyncio

            await asyncio.Event().wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _silent(send: Send) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            kept = [h for h in message["headers"] if h[0].lower() != b"cache-control"]
            message = {**message, "headers": [*kept, NO_STORE]}
        await send(message)

    return wrapped


async def _reply(send: Send, status: int, body: dict, headers: list[tuple[bytes, bytes]] = []) -> None:  # noqa: B006
    data = json.dumps(body).encode()
    base = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()), NO_STORE]
    await send({"type": "http.response.start", "status": status, "headers": [*base, *headers]})
    await send({"type": "http.response.body", "body": data})
