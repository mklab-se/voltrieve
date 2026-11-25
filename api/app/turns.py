"""Turn state and the runner that drives a backend turn into an SSE stream.

State lives in this process: `TurnStore` is a dict, so the API runs with maxReplicas 1 and
a restart loses paused turns. Nothing durable is lost: conversation ownership is stored in
Foundry with the conversation, and a conversation touched for the first time by this
process (or left dirty by an abandoned, expired or failed turn) is repaired before use,
which gives every open function call an output.
"""

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from voltrieve_agent.events import KEEPALIVE, KEEPALIVE_SECONDS, REVIEWING, THINKING, AgentEvent, status, format_event, trace
from voltrieve_agent.model import merge_usage
from voltrieve_agent.private import log_exception, pseudonym
from voltrieve_agent.tools import ClientTool, UserContext
from voltrieve_agent.unstored import head_outputs

from app.auth import Claims
from app.backends import BackendRegistry
from app.backends.base import ABANDONED_OUTPUT, TurnContext
from app.clients import conversation_metadata
from app.logging_setup import bind_log_context, correlation_id
from app.sources import LOOKUP_TIMEOUT_SECONDS, SourceService
from app.insights import LiveFeed, TurnRecorder, mask_prompt
from app.telemetry import annotate_span, debug_payload, set_span_attributes, span_event, turn_span

logger = logging.getLogger(__name__)

TURN_TTL_SECONDS = 15 * 60
MAX_CONVERSATIONS = 1000
MAX_UNSTORED_PAUSES = 3  # per user: each keeps its attachments in memory
GENERIC_ERROR = AgentEvent(
    "error", {"code": "internal_error", "message": "The request could not be completed."}
)


@dataclass
class PausedTurn:
    turn_id: str
    oid: str
    conversation_id: str
    backend: str
    client_tools: list[ClientTool]
    pending_outputs: list[dict]
    expected_call_ids: frozenset[str]
    usage: dict = field(default_factory=dict)
    text: str = ""  # the answer so far, so a continuation still finds its citations
    kept: dict | None = field(default=None, repr=False)  # what an unstored turn keeps in memory
    context: dict = field(default_factory=dict, repr=False)  # what the client said when the turn began
    expires_at: float = 0.0
    ordering_client: str = ""  # of the turn so far
    private_tool: bool = False  # the pending outputs may hold what a private tool said

    @property
    def private(self) -> bool:
        return bool(self.kept is not None or self.context or self.private_tool)

    def abandon_outputs(self) -> list[dict]:
        """Outputs that close every open function_call of this turn. An unstored turn has stored
        only the calls whose outputs are in its head, so only those are closed."""
        if self.kept is not None:
            return head_outputs(self.kept)
        return [
            *self.pending_outputs,
            *(
                {"type": "function_call_output", "call_id": call_id, "output": ABANDONED_OUTPUT}
                for call_id in sorted(self.expected_call_ids)
            ),
        ]


@dataclass
class _Conversation:
    oid: str
    dirty: bool


class TurnStore:
    def __init__(
        self,
        ttl: float = TURN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_conversations: int = MAX_CONVERSATIONS,
    ):
        self._ttl = ttl
        self._clock = clock
        self._max_conversations = max_conversations
        self._paused: dict[str, PausedTurn] = {}
        self._paused_by_conversation: dict[str, str] = {}
        # A bounded LRU. Foundry holds the truth; a miss costs one lookup and a repair.
        self._conversations: OrderedDict[str, _Conversation] = OrderedDict()

    def register_conversation(self, conversation_id: str, oid: str, dirty: bool = False) -> None:
        self._conversations[conversation_id] = _Conversation(oid, dirty)
        self._conversations.move_to_end(conversation_id)
        while len(self._conversations) > self._max_conversations:
            self._conversations.popitem(last=False)

    def conversation_owner(self, conversation_id: str) -> str | None:
        entry = self._conversations.get(conversation_id)
        if entry is None:
            return None
        self._conversations.move_to_end(conversation_id)
        return entry.oid

    def mark_dirty(self, conversation_id: str) -> None:
        if conversation_id in self._conversations:
            self._conversations[conversation_id].dirty = True

    def mark_clean(self, conversation_id: str) -> None:
        if conversation_id in self._conversations:
            self._conversations[conversation_id].dirty = False

    def needs_repair(self, conversation_id: str) -> bool:
        entry = self._conversations.get(conversation_id)
        return entry is not None and entry.dirty

    def put(self, paused: PausedTurn) -> None:
        self.evict_expired()
        paused.expires_at = self._clock() + self._ttl
        self._paused[paused.turn_id] = paused
        self._paused_by_conversation[paused.conversation_id] = paused.turn_id
        unstored = [p for p in self._paused.values() if p.oid == paused.oid and p.kept is not None]
        for oldest in unstored[:-MAX_UNSTORED_PAUSES]:
            self._drop(oldest)

    def get(self, turn_id: str) -> PausedTurn | None:
        return self._paused.get(turn_id)

    def is_expired(self, paused: PausedTurn) -> bool:
        return self._clock() >= paused.expires_at

    def remove(self, turn_id: str) -> PausedTurn | None:
        paused = self._paused.pop(turn_id, None)
        if paused and self._paused_by_conversation.get(paused.conversation_id) == turn_id:
            del self._paused_by_conversation[paused.conversation_id]
        return paused

    def remove_for_conversation(self, conversation_id: str) -> PausedTurn | None:
        """Take the paused turn of a conversation, expired or not (it still needs closing)."""
        turn_id = self._paused_by_conversation.get(conversation_id)
        return self.remove(turn_id) if turn_id else None

    def evict_expired(self) -> int:
        """Drop expired pauses. Their conversations are left dirty, so the next touch repairs."""
        expired = [p for p in self._paused.values() if self.is_expired(p)]
        for paused in expired:
            self._drop(paused)
        return len(expired)

    def _drop(self, paused: PausedTurn) -> None:
        self.remove(paused.turn_id)
        self.mark_dirty(paused.conversation_id)


@dataclass(frozen=True)
class Answer:
    """What one turn said, for a caller without a stream."""

    text: str = field(repr=False)
    titles: list[str]  # of the documents it cites
    approved: bool  # the text is an approved answer


@dataclass
class Job:
    claims: Claims
    backend: str
    conversation_id: str | None  # None: create one
    turn_id: str
    input: str | list[dict]
    client_tools: list[ClientTool] = field(default_factory=list)
    pending_outputs: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)  # of the earlier parts of this turn
    text: str = ""  # the answer from the earlier parts of this turn
    stale: PausedTurn | None = None  # a paused turn to close before running
    turn_expired: bool = False  # answer turn_expired instead of running
    expired: PausedTurn | None = None  # with turn_expired: also close this turn
    # Attachment parts, what an unstored turn kept, and structured context. Never logged.
    parts: list[dict] = field(default_factory=list, repr=False)
    kept: dict | None = field(default=None, repr=False)
    context: dict = field(default_factory=dict, repr=False)
    context_line: str = field(default="", repr=False)  # goes before the message, to the backend only
    attachments: list[dict] = field(default_factory=list)  # kind, detail and size of each, for the trace
    client: str = ""  # car, web, cli, mcp, other or test
    ordering_client: str = ""  # car, web, cli, mcp or other: what the tools are told
    private_tool: bool = False  # a private tool was called in this turn
    through_a_door: bool = False  # nothing of it is logged, not even the question at debug level

    @property
    def unstored(self) -> bool:
        return bool(self.parts or self.kept)

    @property
    def private(self) -> bool:
        """Whether nothing the turn says may be logged, error texts included."""
        return bool(self.unstored or self.context or self.private_tool or self.through_a_door)


class _Run:
    """The events of one request: a queue the response drains, fed by a background task."""

    def __init__(self, recorder: TurnRecorder | None = None, private: bool = False) -> None:
        self.queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._start = time.perf_counter()
        self.held_tool_calls: AgentEvent | None = None
        self.recorder = recorder
        self.private = private
        self.text = ""  # the answer so far, to find the documents it cites

    async def emit(self, event: AgentEvent) -> None:
        if event.type == "delta":
            self.text += str(event.data.get("text", ""))
        if self.recorder is not None:
            self.recorder.observe(event)
        if event.type == "trace" and "elapsed_ms" not in event.data:
            elapsed = round((time.perf_counter() - self._start) * 1000, 1)
            event = AgentEvent("trace", {**event.data, "elapsed_ms": elapsed})
        if event.type == "trace":
            # From the first private tool on, the rest of the request is private.
            self.private = self.private or event.data.get("private") is True
            self._record(event.data)
        if event.type == "client_tool_calls":
            # Held until the paused turn is stored, so a fast client never beats the store.
            self.held_tool_calls = event
            return
        await self.queue.put(event)

    def _record(self, data: dict) -> None:
        """Every hop is a log line and a span event, so logs and traces show the whole turn."""
        step = data["step"]
        logger.info(
            "trace",
            extra={
                "step": step,
                "component": data.get("component"),
                "detail": data.get("detail"),
                "elapsed_ms": data.get("elapsed_ms"),
            },
        )
        span_event(
            step,
            component=data.get("component"),
            detail=data.get("detail"),
            elapsed_ms=data.get("elapsed_ms"),
            **({k: data.get(k) for k in ("kind", "size")} if step == "attachment_used" else {}),
        )
        if self.private:
            return  # arguments and results may say what is in an attachment or the context
        # Arguments and results can hold user data: only at debug level, truncated.
        for key in ("arguments", "output_preview"):
            if key in data:
                debug_payload(logger, f"{step} {key}", data[key])


class TurnService:
    def __init__(self, store: TurnStore, registry: BackendRegistry, feed: LiveFeed | None = None,
                 model: str = "gpt-6.1-terra", sources: SourceService | None = None):
        self.store = store
        self.sources = sources
        self._registry = registry
        self.feed = feed or LiveFeed()
        self._model = model
        self._cold = True  # the first turn after the process started
        self._tasks: set[asyncio.Task] = set()
        self._running: dict[str, _Run] = {}  # conversation id -> its running turn

    def is_running(self, conversation_id: str) -> bool:
        return conversation_id in self._running

    def start(self, job: Job) -> AsyncIterator[bytes]:
        """Start the turn in the background and return its SSE byte stream.

        The turn is not tied to the response: if the client disconnects, it still runs to
        its end, so the conversation is never left with a function call without output.
        """
        return self._drain(self._begin(job))

    async def answer(self, job: Job, seconds: float) -> Answer | None:
        """One whole turn, for a caller without a stream. None when it gave no answer. After `seconds` it
        is a TimeoutError, and the turn runs on to its end, as when a client disconnects."""
        run = self._begin(job)
        titles: list[str] = []
        done = approved = False
        async with asyncio.timeout(seconds):
            while (event := await run.queue.get()) is not None:
                if event.type == "error":
                    return None  # the turn may have more to clean up, which nobody waits for
                if event.type == "sources":
                    titles = [i["id"] for i in event.data["items"] if i["kind"] == "source"]
                elif event.type == "trace" and event.data.get("step") == "final_answer_sent":
                    approved = True
                elif event.type == "done":
                    done = True
        return Answer(run.text, titles, approved) if done and run.text else None

    def _begin(self, job: Job) -> _Run:
        cold, self._cold = self._cold, False
        user = pseudonym(job.claims.oid)
        kind = "message" if isinstance(job.input, str) else "tool_results"
        # An expired turn runs nothing, and its turn id is client input: it stays off the feed.
        recorder = None if job.turn_expired else TurnRecorder(
            job.backend, self._model, user, cold_start=cold, kind=kind, client=job.client)
        run = _Run(recorder, job.private)
        run.text = job.text
        if job.turn_expired:
            pass  # nothing to show
        elif kind == "tool_results" and self.feed.has(job.turn_id):
            # A client tool round of a turn already on the feed: keep its prompt.
            self.feed.publish({"turn_id": job.turn_id, "outcome": "running"})
        else:
            self.feed.publish({
                "turn_id": job.turn_id, "user": user, "backend": job.backend, "kind": kind,
                "client": job.client,
                "prompt": mask_prompt(job.input) if kind == "message" else None, "outcome": "running",
            })
        # A turn_expired job runs nothing, and its conversation id is unvalidated client input.
        if job.conversation_id is not None and not job.turn_expired:
            self._running[job.conversation_id] = run
        task = asyncio.create_task(self._execute(run, job, correlation_id.get()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def drain(self, timeout: float = 20.0) -> None:
        """Wait for running turns, for shutdown."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    @staticmethod
    async def _drain(run: _Run) -> AsyncIterator[bytes]:
        while True:
            try:
                event = await asyncio.wait_for(run.queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield KEEPALIVE
                continue
            if event is None:
                return
            yield format_event(event)

    def _release(self, conversation_id: str | None, run: _Run) -> None:
        if conversation_id is not None and self._running.get(conversation_id) is run:
            del self._running[conversation_id]

    async def _execute(self, run: _Run, job: Job, cid: str) -> None:
        correlation_id.set(cid)
        with turn_span(
            conversation_id=job.conversation_id,
            turn_id=job.turn_id,
            backend=job.backend,
            correlation_id=cid,
        ):
            try:
                await self._run_job(run, job)
            finally:
                if run.recorder is not None:
                    summary = run.recorder.finish()
                    set_span_attributes(summary.span_attributes())
                    self.feed.publish({"turn_id": job.turn_id, **summary.feed_fields()})

    async def _run_job(self, run: _Run, job: Job) -> None:
        oid = job.claims.oid
        conversation_id = job.conversation_id
        bind_log_context(
            user=pseudonym(oid),
            backend=job.backend,
            turn_id=job.turn_id,
            conversation_id=conversation_id or "",
        )
        try:
            await run.emit(
                trace(
                    "token_validated",
                    "auth",
                    "roles: " + ", ".join(job.claims.roles),
                    roles=job.claims.roles,
                    user=job.claims.username,
                )
            )
            await run.emit(trace("backend_selected", "api", job.backend, backend=job.backend))
            if job.turn_expired:
                if job.expired is not None:
                    await self._close(job.expired, run)
                await run.emit(
                    AgentEvent(
                        "error",
                        {
                            "code": "turn_expired",
                            "message": "This turn is no longer available. Send the message again.",
                        },
                    )
                )
                return
            # First, before anything slow: a user who sees this knows the request arrived.
            await run.emit(status(THINKING if isinstance(job.input, str) else REVIEWING))
            backend = self._registry.get_backend(job.backend)
            if job.stale is not None:
                await self._close(job.stale, run)
            if conversation_id and self.store.needs_repair(conversation_id):
                try:
                    await backend.repair(conversation_id)
                except Exception:
                    log_exception(logger, "Conversation repair failed", private=job.private)
                    self.store.mark_dirty(conversation_id)
                    await run.emit(
                        AgentEvent(
                            "error",
                            {
                                "code": "conversation_recovery_failed",
                                "message": "The conversation could not be recovered. "
                                "Start a new conversation.",
                            },
                        )
                    )
                    return
                self.store.mark_clean(conversation_id)
                await run.emit(trace("conversation_repaired", "api", conversation_id))

            if conversation_id is None:
                conversation_id = await backend.create_conversation(
                    conversation_metadata(oid, job.client, job.backend, (job.context.get("car") or {}).get("model"))
                )
                self.store.register_conversation(conversation_id, oid)
                self._running[conversation_id] = run
                bind_log_context(conversation_id=conversation_id)
                annotate_span(conversation_id=conversation_id)
                await run.emit(trace("conversation_created", "api", conversation_id))

            # The input of an unstored turn may be about the attachment, and the tool results of a
            # private one may say what the client told about itself.
            if not job.unstored and not job.through_a_door and not (job.private and isinstance(job.input, list)):
                debug_payload(logger, "turn input", job.input)
            for used in job.attachments:
                await run.emit(trace("attachment_used", "api", **used))
            input_ = job.input
            if job.context_line and isinstance(input_, str):
                input_ = f"{job.context_line}\n{input_}"
            ctx = TurnContext(
                user=UserContext(
                    oid,
                    job.claims.username,
                    frozenset(job.claims.roles),
                    job.claims.name,
                    job.ordering_client,
                ),
                conversation_id=conversation_id,
                turn_id=job.turn_id,
                input=input_,
                client_tools=job.client_tools,
                pending_outputs=job.pending_outputs,
                correlation_id=correlation_id.get(),
                parts=job.parts,
                kept=job.kept,
                context=job.context,
                private_tool=job.private_tool,
            )
            outcome = await backend.run_turn(ctx, run.emit)
            run.private = run.private or outcome.private
            job.private_tool = job.private_tool or run.private
            usage = merge_usage(job.usage, outcome.usage)
            if outcome.status == "paused":
                # No await between releasing the conversation and storing the pause, so a
                # fast continuation never meets a "running" turn.
                self._release(conversation_id, run)
                self.store.put(
                    PausedTurn(
                        job.turn_id,
                        oid,
                        conversation_id,
                        job.backend,
                        job.client_tools,
                        outcome.pending_outputs,
                        frozenset(outcome.client_call_ids),
                        usage,
                        run.text,
                        kept=outcome.kept,
                        context=job.context,
                        ordering_client=job.ordering_client,
                        private_tool=job.private_tool,
                    )
                )
                if run.held_tool_calls is not None:
                    # A first turn has no `done` before this, so the client learns the
                    # conversation id here.
                    data = {**run.held_tool_calls.data, "conversation_id": conversation_id}
                    await run.queue.put(AgentEvent("client_tool_calls", data))
            elif outcome.status == "done":
                if outcome.unrecorded:
                    # The answer was given but not recorded: repair before the conversation is used.
                    self.store.mark_dirty(conversation_id)
                await self._send_sources(run, job)
                await run.emit(
                    AgentEvent(
                        "done",
                        {
                            "conversation_id": conversation_id,
                            "turn_id": job.turn_id,
                            "backend": job.backend,
                            "usage": usage,
                        },
                    )
                )
            else:
                # "failed": the loop has already emitted the error event. The turn may have
                # left function calls without output (or the results may not have reached the
                # conversation), so check it before its next use.
                self.store.mark_dirty(conversation_id)
        except Exception:
            log_exception(logger, "Turn failed", private=run.private)
            if conversation_id:
                self.store.mark_dirty(conversation_id)
            if run.recorder is not None:
                run.recorder.observe(GENERIC_ERROR)
            await run.queue.put(GENERIC_ERROR)
        finally:
            self._release(job.conversation_id, run)
            self._release(conversation_id, run)
            await run.queue.put(None)

    async def _send_sources(self, run: _Run, job: Job) -> None:
        """The documents the answer cites, after the text, so the first text is never later.
        A failed lookup costs the links, never the answer."""
        if self.sources is None or not run.text:
            return
        try:
            items = await asyncio.wait_for(self.sources.resolve(run.text), LOOKUP_TIMEOUT_SECONDS)
        except Exception:
            log_exception(logger, "Source lookup failed", level=logging.WARNING, private=job.private)
            return
        if not items:
            return
        kinds = [i["kind"] for i in items]
        await run.emit(
            trace(
                "sources_resolved",
                "api",
                f"{kinds.count('source')} sources, {kinds.count('read_more')} to read more",
                sources=len(items),
            )
        )
        await run.emit(AgentEvent("sources", {"turn_id": job.turn_id, "items": items}))

    async def _close(self, paused: PausedTurn, run: _Run) -> None:
        """Give the open function calls of an abandoned or expired turn an output."""
        try:
            outputs = paused.abandon_outputs()
            backend = self._registry.get_backend(paused.backend)
            if outputs:  # an unstored turn may have nothing in the conversation to close
                await backend.abandon(paused.conversation_id, outputs)
        except Exception:
            log_exception(
                logger, "Could not close the abandoned turn %s", paused.turn_id, private=paused.private
            )
            self.store.mark_dirty(paused.conversation_id)
            return
        await run.emit(
            trace(
                "turn_abandoned",
                "api",
                f"{'closed' if outputs else 'dropped'} unfinished turn {paused.turn_id}",
                turn_id=paused.turn_id,
            )
        )


def new_turn_id() -> str:
    return str(uuid.uuid4())
