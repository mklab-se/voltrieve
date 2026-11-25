"""What the operations dashboard shows about each turn, and the live feed of turns.

The telemetry we write (the chat.turn span) carries ids, names, counts, timings and an estimated cost,
never text. Here the prompt only exists in the in-memory live feed, masked, and only reaches a
dashboard that asks for it (docs/superpowers/specs/2026-11-17-operations-dashboard-design.md).
Foundry's own tracing, which recorded the text of prompt and hosted turns, is disconnected (docs/decisions.md).
"""

import asyncio
import datetime as dt
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from voltrieve_agent.events import AgentEvent
from voltrieve_agent.private import mask_contacts

# USD per million tokens: input, cached input, output (list prices, Global Standard, Sweden
# Central, read 2026-11-05; scripts/model_costs.py uses the same table).
PRICES = {
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
}
# Prices the planning of a retrieve that does not name its model, or names one without a price.
PLANNING_MODEL = "gpt-5.4-mini"
SEARCH_REASONING_PER_MILLION = 0.022
USD_TO_SEK = 10.5  # a fixed rate: the estimate is for trends, not the bill

PROMPT_LIMIT = 240


def mask_prompt(text: str) -> str:
    """The prompt as the live feed may show it: contact details and id numbers replaced."""
    masked = " ".join(mask_contacts(text).split())
    return masked if len(masked) <= PROMPT_LIMIT else masked[:PROMPT_LIMIT] + "…"


def estimate_cost_sek(model: str, usage: dict, planning_input: int = 0, planning_output: int = 0,
                      search_reasoning: int = 0, planning_model: str | None = None) -> float | None:
    """Estimated cost of a turn in SEK from token counts and list prices; None for an unknown model."""
    if model not in PRICES:
        return None
    price_in, price_cached, price_out = PRICES[model]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    uncached = usage.get("input_tokens", 0) - cached
    usd = (uncached * price_in + cached * price_cached + usage.get("output_tokens", 0) * price_out) / 1e6
    plan_in, _, plan_out = PRICES.get(planning_model or PLANNING_MODEL, PRICES[PLANNING_MODEL])
    usd += (planning_input * plan_in + planning_output * plan_out) / 1e6
    usd += search_reasoning * SEARCH_REASONING_PER_MILLION / 1e6
    return round(usd * USD_TO_SEK, 5)


@dataclass
class TurnSummary:
    backend: str
    model: str
    user: str
    outcome: str
    error_code: str | None
    first_status_ms: int | None
    first_text_ms: int | None
    duration_ms: int
    model_calls: int
    tools: list[str]
    knowledge_bases: list[str]
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    search_reasoning_tokens: int
    cost_sek: float | None
    cold_start: bool = False
    kind: str = "message"  # message, or tool_results: a client tool round of an earlier message
    client: str = ""  # car, web, cli, mcp, other or test

    def span_attributes(self) -> dict:
        """OpenTelemetry GenAI names where one exists, voltrieve.* for the rest. No text."""
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": self.model,
            "gen_ai.usage.input_tokens": self.input_tokens,
            "gen_ai.usage.output_tokens": self.output_tokens,
            "voltrieve.backend": self.backend,
            "voltrieve.user": self.user,
            "voltrieve.outcome": self.outcome,
            "voltrieve.duration_ms": self.duration_ms,
            "voltrieve.model_calls": self.model_calls,
            "voltrieve.tools": ",".join(self.tools),
            "voltrieve.knowledge_bases": ",".join(self.knowledge_bases),
            "voltrieve.cached_tokens": self.cached_tokens,
            "voltrieve.search_reasoning_tokens": self.search_reasoning_tokens,
            "voltrieve.cold_start": self.cold_start,
            "voltrieve.kind": self.kind,
            "voltrieve.client": self.client,
        }
        optional = {
            "voltrieve.error_code": self.error_code,
            "voltrieve.first_status_ms": self.first_status_ms,
            "voltrieve.first_text_ms": self.first_text_ms,
            "voltrieve.cost_sek": self.cost_sek,
        }
        attrs.update({k: v for k, v in optional.items() if v is not None})
        return attrs

    def feed_fields(self) -> dict:
        return {
            "outcome": self.outcome, "error_code": self.error_code,
            "first_text_ms": self.first_text_ms, "duration_ms": self.duration_ms,
            "tools": self.tools, "knowledge_bases": self.knowledge_bases,
            "cost_sek": self.cost_sek, "model_calls": self.model_calls,
            "client": self.client,
        }


@dataclass
class TurnRecorder:
    """Watches the events of one request and sums up what the dashboard needs."""

    backend: str
    model: str
    user: str
    clock: Callable[[], float] = time.perf_counter
    cold_start: bool = False
    kind: str = "message"
    client: str = ""
    _start: float = field(init=False)
    _first_status: float | None = field(default=None, init=False)
    _first_text: float | None = field(default=None, init=False)
    _outcome: str = field(default="answered", init=False)
    _error: str | None = field(default=None, init=False)
    _model_calls: int = field(default=0, init=False)
    _tools: list[str] = field(default_factory=list, init=False)
    _kbs: list[str] = field(default_factory=list, init=False)
    _usage: dict = field(default_factory=dict, init=False)
    _planning: list[int] = field(default_factory=lambda: [0, 0, 0], init=False)
    _planning_model: str | None = field(default=None, init=False)
    _summary: TurnSummary | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._start = self.clock()

    def observe(self, event: AgentEvent) -> None:
        data = event.data
        if event.type == "status" and self._first_status is None:
            self._first_status = self.clock()
        elif event.type == "delta" and self._first_text is None:
            self._first_text = self.clock()
        elif event.type == "done":
            self._usage = data.get("usage") or {}
        elif event.type == "error":
            self._outcome, self._error = "error", str(data.get("code") or "error")
        elif event.type == "client_tool_calls" and self._outcome == "answered":
            self._outcome = "client_tool"
        elif event.type == "trace":
            self._trace(data)

    def _trace(self, data: dict) -> None:
        step = data.get("step")
        if step == "model_called":
            self._model_calls += 1
        elif step == "server_tool_called" and data.get("tool"):
            _add(self._tools, data["tool"])
        elif step == "knowledge_base_retrieved":
            _add(self._kbs, data.get("knowledge_base"))
            self._planning[0] += data.get("planning_input_tokens", 0)
            self._planning[1] += data.get("planning_output_tokens", 0)
            self._planning[2] += data.get("search_reasoning_tokens", 0)
            self._planning_model = data.get("planning_model") or self._planning_model
        elif step == "mcp_tool_called":
            _add(self._kbs, data.get("server"))
        elif step == "final_answer_sent" and self._outcome == "answered":
            self._outcome = "curated"

    def finish(self) -> TurnSummary:
        if self._summary is None:
            end = self.clock()
            ms = lambda t: None if t is None else round((t - self._start) * 1000)  # noqa: E731
            cached = (self._usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            self._summary = TurnSummary(
                self.backend, self.model, self.user, self._outcome, self._error,
                ms(self._first_status), ms(self._first_text), ms(end) or 0, self._model_calls,
                list(self._tools), list(self._kbs), self._usage.get("input_tokens", 0), cached,
                self._usage.get("output_tokens", 0), self._planning[2],
                estimate_cost_sek(self.model, self._usage, *self._planning, self._planning_model),
                self.cold_start, self.kind, self.client,
            )
        return self._summary


def _add(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


class LiveFeed:
    """The last turns, kept in memory only, and the dashboards streaming them.

    An entry is published when a turn starts and again, merged, when it ends. Subscribers that
    did not ask for prompts get every entry with the prompt removed.
    """

    def __init__(self, size: int = 50, clock: Callable[[], float] = time.time):
        self._size = size
        self._clock = clock
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._seen: dict[str, float] = {}
        self._subscribers: list[tuple[asyncio.Queue, bool]] = []
        self._day = ""
        self._turns_today = 0

    def publish(self, entry: dict) -> dict:
        turn_id = entry["turn_id"]
        if turn_id not in self._entries and entry.get("kind") == "message":
            today = dt.date.fromtimestamp(self._clock()).isoformat()
            if today != self._day:
                self._day, self._turns_today = today, 0
            self._turns_today += 1
        merged = {**self._entries.get(turn_id, {"at": self._clock()}), **entry}
        self._entries[turn_id] = merged
        self._entries.move_to_end(turn_id)
        while len(self._entries) > self._size:
            self._entries.popitem(last=False)
        if entry.get("user"):
            self._seen[entry["user"]] = self._clock()
        for queue, show in list(self._subscribers):
            if queue.qsize() < 200:  # a stalled dashboard never grows memory without bound
                queue.put_nowait(_view(merged, show))
        return merged

    def has(self, turn_id: str) -> bool:
        return turn_id in self._entries

    def recent(self, show_prompts: bool) -> list[dict]:
        return [_view(e, show_prompts) for e in self._entries.values()]

    def active_users(self, window: float = 300.0) -> int:
        since = self._clock() - window
        self._seen = {u: t for u, t in self._seen.items() if t >= since}
        return len(self._seen)

    def summary(self) -> dict:
        """Users active in the last 5 minutes and messages since midnight (in this process)."""
        today = dt.date.fromtimestamp(self._clock()).isoformat()
        return {"active_users": self.active_users(), "turns": self._turns_today if today == self._day else 0}

    def subscribe(self, show_prompts: bool) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.append((queue, show_prompts))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = [(q, s) for q, s in self._subscribers if q is not queue]


def _view(entry: dict, show_prompts: bool) -> dict:
    return {**entry, "prompt": entry.get("prompt") if show_prompts else None}
