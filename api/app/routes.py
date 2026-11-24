import asyncio
import datetime as dt
import json
import logging

from pydantic import BaseModel, ValidationError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from voltrieve_agent.events import AgentEvent, format_event
from voltrieve_agent.private import log_exception

from app.attachments import Rejected
from app.attachments.store import Stored, attachment_parts
from app.attachments.upload import INTERRUPTED, refuse
from app.auth import AuthError, Claims
from app.backends import BACKEND_NAMES
from app.backends.base import BackendUnavailable
from app.clients import client_name, is_test
from app.context import context_line
from app.logging_setup import correlation_id
from app.schemas import (
    MAX_CLIENT_TOOLS_BYTES,
    ChatRequest,
    ToolResultsRequest,
    to_client_tools,
)
from app.sources import LINK_SECONDS, LinkRefused
from app.turns import Job, new_turn_id

logger = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def authenticate(request: Request, authorization: str | None = Header(default=None)) -> Claims:
    try:
        return await request.app.state.validator.validate(authorization)
    except AuthError as exc:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        raise HTTPException(exc.status_code, exc.detail, headers=headers) from exc


MAX_SCHEMA_DEPTH = 32


def _depth(value) -> int:
    """Nesting depth of parsed JSON, computed without recursion."""
    deepest = 0
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            deepest = max(deepest, depth)
            if deepest > MAX_SCHEMA_DEPTH:
                return deepest
            stack.extend((child, depth + 1) for child in (node.values() if isinstance(node, dict) else node))
    return deepest


REF_KEYWORDS = ("$ref", "$dynamicRef")


def _has_ref(value) -> bool:
    """Whether a reference keyword appears anywhere in parsed JSON (iterative, like _depth)."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if any(k in node for k in REF_KEYWORDS):
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


def json_body(model: type[BaseModel]):
    """Parse the body by hand: FastAPI turns a RecursionError from deeply nested JSON into a
    400 or 500, and clients should get a 422."""

    async def parse(request: Request) -> BaseModel:
        try:
            return model.model_validate(json.loads(await request.body()))
        except RecursionError as exc:
            raise HTTPException(422, "JSON is nested too deeply") from exc
        except ValidationError as exc:
            # Without the input: it may hold what the client knows about the user, and values that
            # JSON cannot carry.
            errors = exc.json(include_url=False, include_context=False, include_input=False)
            raise HTTPException(422, json.loads(errors)) from exc
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc

    return parse


def _check_client_tools(body: ChatRequest, server_tool_names: frozenset[str]) -> None:
    for tool in body.client_tools:
        if _depth(tool.parameters) > MAX_SCHEMA_DEPTH:
            raise HTTPException(422, f"client tool '{tool.name}' schema is nested too deeply")
        if _has_ref(tool.parameters):
            # A reference could make the validator fetch a URL of the client's choosing.
            raise HTTPException(422, f"client tool '{tool.name}' schema must not use $ref")
    names = [t.name for t in body.client_tools]
    if len(set(names)) != len(names):
        raise HTTPException(422, "client_tools has duplicate names")
    clashes = sorted(set(names) & server_tool_names)
    if clashes:
        raise HTTPException(422, f"client_tools collide with server tools: {', '.join(clashes)}")
    size = len(json.dumps([t.model_dump() for t in body.client_tools]).encode())
    if size > MAX_CLIENT_TOOLS_BYTES:
        raise HTTPException(422, f"client_tools exceed {MAX_CLIENT_TOOLS_BYTES} bytes")
    for tool in body.client_tools:
        try:
            Draft202012Validator.check_schema(tool.parameters)
        except SchemaError as exc:
            raise HTTPException(422, f"client tool '{tool.name}' has an invalid schema") from exc


async def _check_owner(state, backend, conversation_id: str, oid: str) -> None:
    """Refuse conversations of other users with the same answer as unknown ones, so ids
    cannot be probed. Foundry holds the owner; the in-process map is only a cache."""
    owner = state.store.conversation_owner(conversation_id)
    if owner is None:
        try:
            owner = await backend.get_conversation_owner(conversation_id)
        except Exception as exc:
            # Foundry is down or failing: that is not "unknown conversation".
            logger.warning("Could not look up the owner of %s", conversation_id, exc_info=True)
            raise HTTPException(503, "The conversation store is unavailable. Try again later.") from exc
        if owner == oid:
            # First touch in this process: the conversation is repaired before it is used.
            state.store.register_conversation(conversation_id, oid, dirty=True)
    if owner != oid:
        raise HTTPException(403, "Unknown conversation")


def _requested_backend(request: Request, header: str | None) -> str:
    """The backend the client asked for when it names a real one, else the default."""
    return header if header in BACKEND_NAMES else request.app.state.settings.agent_backend


def _attachments(state, oid: str, ids: list[str]) -> list[Stored]:
    """The named attachments. An unknown, expired or someone else's id gives the same answer, so ids
    cannot be probed."""
    found = [state.attachments.get(oid, id) for id in ids]
    if any(stored is None for stored in found):
        raise HTTPException(422, {"code": "unknown_attachment"})
    return found


async def _store_upload(request: Request, oid: str) -> dict:
    """Read, process and keep an upload, and return the answer to the client. An upload is logged
    with its kind, size, detail, status and code, and nothing else."""
    state = request.app.state
    with state.uploads.place(oid):
        upload = await state.uploads.read(request)
        kind = upload.handler.kind
        try:
            parts = await state.uploads.process(request, upload)
        except Rejected as exc:
            logger.info("Attachment rejected: kind %s, %d bytes, code %s", kind, len(upload.data), exc.code)
            return {"id": None, "kind": kind, "status": "rejected", "code": exc.code, "message": exc.message}
        if await request.is_disconnected():
            refuse(400, INTERRUPTED)  # nobody is there to get the id
        stored = state.attachments.put(oid, kind, parts)
    logger.info("Attachment ready: kind %s, %d bytes, %s", kind, stored.size, stored.detail)
    expires = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=state.attachments.ttl)
    return {"id": stored.id, "kind": kind, "status": "ready", "expires_at": expires.isoformat(timespec="seconds")}


def _client(request: Request, claims: Claims, test_header: str | None) -> str:
    return client_name(request.app.state.settings, claims.azp, is_test(test_header))


def _stream(request: Request, job: Job) -> StreamingResponse:
    cid = correlation_id.get()
    return StreamingResponse(
        request.app.state.turns.start(job),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Correlation-Id": cid},
    )


DASHBOARD_ROLE = "Dashboard.View"
SUMMARY_SECONDS = 10.0


def _live(feed, show_prompts: bool, once: bool):
    """The dashboard stream: a summary and the recent turns, then each turn as it changes."""

    async def stream():
        yield format_event(AgentEvent("summary", feed.summary()))
        for entry in feed.recent(show_prompts):
            yield format_event(AgentEvent("turn", entry))
        if once:
            return
        queue = feed.subscribe(show_prompts)
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), SUMMARY_SECONDS)
                except TimeoutError:
                    yield format_event(AgentEvent("summary", feed.summary()))
                    continue
                yield format_event(AgentEvent("turn", entry))
        finally:
            feed.unsubscribe(queue)

    return stream()


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @router.get("/admin/live")
    async def live(
        request: Request,
        claims: Claims = Depends(authenticate),
        prompts: bool = False,
        once: bool = False,
    ) -> StreamingResponse:
        """The live dashboard feed. Prompts are sent, masked, only when asked for."""
        if DASHBOARD_ROLE not in claims.roles:
            raise HTTPException(403, "Missing required role")
        return StreamingResponse(
            _live(request.app.state.turns.feed, prompts, once),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @router.get("/sources/{title}")
    async def source_link(
        title: str,
        request: Request,
        claims: Claims = Depends(authenticate),
    ) -> JSONResponse:
        """A short signed link to one linkable document. The client opens it at once; the
        URL is never logged, and each click gets a fresh one, so old answers keep working."""
        sources = request.app.state.sources
        if sources is None:
            raise HTTPException(503, "Source links are not configured")
        try:
            url = await sources.link(title)
        except KeyError as exc:
            raise HTTPException(404, "Unknown document") from exc
        except LinkRefused as exc:
            raise HTTPException(403, "This document cannot be opened") from exc
        return JSONResponse({"url": url, "expires_in": LINK_SECONDS}, headers={"Cache-Control": "no-store"})

    @router.post("/attachments")
    async def upload_attachment(request: Request, claims: Claims = Depends(authenticate)) -> JSONResponse:
        """An attachment for a later turn. One that is not good enough is an answer, not an error."""
        failed = False
        try:
            answer = await _store_upload(request, claims.oid)
        except HTTPException:
            raise
        except Exception:
            failed = True
            log_exception(logger, "Attachment failed", private=True)  # its text may quote the attachment
        if failed:  # outside the except block, so that nothing is chained to it
            refuse(500, "The attachment could not be processed.")
        return JSONResponse(answer, 201)

    @router.post("/chat")
    async def chat(
        request: Request,
        claims: Claims = Depends(authenticate),
        body: ChatRequest = Depends(json_body(ChatRequest)),
        x_agent_backend: str | None = Header(default=None),
        x_voltrieve_test: str | None = Header(default=None),
    ):
        state = request.app.state
        _check_client_tools(body, state.server_tool_names)
        name = x_agent_backend or state.settings.agent_backend
        try:
            backend = state.registry.get_backend(name)
        except BackendUnavailable as exc:
            raise HTTPException(400, str(exc)) from exc
        attachments = _attachments(state, claims.oid, body.attachments)

        stale = None
        if body.conversation_id is not None:
            await _check_owner(state, backend, body.conversation_id, claims.oid)
            if state.turns.is_running(body.conversation_id):
                raise HTTPException(409, "turn_in_progress")
            stale = state.store.remove_for_conversation(body.conversation_id)
        return _stream(
            request,
            Job(
                claims,
                name,
                body.conversation_id,
                new_turn_id(),
                body.message,
                to_client_tools(body.client_tools),
                stale=stale,
                parts=attachment_parts(attachments),
                attachments=[a.summary() for a in attachments],
                context=body.context.model_dump(exclude_none=True) if body.context else {},
                context_line=context_line(body.context),
                client=_client(request, claims, x_voltrieve_test),
            ),
        )

    @router.post("/chat/tool-results")
    async def tool_results(
        request: Request,
        claims: Claims = Depends(authenticate),
        body: ToolResultsRequest = Depends(json_body(ToolResultsRequest)),
        x_agent_backend: str | None = Header(default=None),
        x_voltrieve_test: str | None = Header(default=None),
    ):
        store = request.app.state.store
        paused = store.get(body.turn_id)
        if paused is None or paused.oid != claims.oid:
            # Unknown, the API restarted, or another user's turn: all get the same answer, so
            # turn ids cannot be probed. Nothing to close, only tell the client.
            return _stream(
                request,
                Job(claims, _requested_backend(request, x_agent_backend), body.conversation_id,
                    body.turn_id, "", turn_expired=True),
            )
        if paused.conversation_id != body.conversation_id:
            raise HTTPException(422, "conversation_id does not match the turn")
        if store.is_expired(paused):
            store.remove(body.turn_id)
            return _stream(
                request,
                Job(claims, paused.backend, paused.conversation_id, paused.turn_id, "",
                    turn_expired=True, expired=paused),
            )
        call_ids = [r.call_id for r in body.results]
        if len(set(call_ids)) != len(call_ids) or set(call_ids) != paused.expected_call_ids:
            raise HTTPException(
                422,
                "results must contain exactly one result for each pending call: "
                + ", ".join(sorted(paused.expected_call_ids)),
            )
        if request.app.state.turns.is_running(paused.conversation_id):
            raise HTTPException(409, "turn_in_progress")
        # Before the pause is taken, so it can still be answered with another id.
        attachments = _attachments(request.app.state, claims.oid, body.attachment_ids())
        store.remove(body.turn_id)
        items = [
            {"type": "function_call_output", "call_id": r.call_id, "output": r.output_text()}
            for r in body.results
        ]
        return _stream(
            request,
            Job(
                claims,
                paused.backend,
                paused.conversation_id,
                paused.turn_id,
                items,
                paused.client_tools,
                paused.pending_outputs,
                usage=paused.usage,
                text=paused.text,
                parts=attachment_parts(attachments),
                kept=paused.kept,
                attachments=[a.summary() for a in attachments],
                context=paused.context,
                client=_client(request, claims, x_voltrieve_test),
            ),
        )

    return router
