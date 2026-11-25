"""Fixed queries on our own telemetry, for the operations door.

Nothing a caller sends is part of a query, except a period from a fixed set and an id of a fixed shape. The
queries turn names and addresses into kinds before anything leaves Log Analytics.
"""

import asyncio
import logging
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx
from voltrieve_doors import Refused

from app.usage import QUERY_URL

logger = logging.getLogger(__name__)

PERIODS = {"1h": "1h", "24h": "24h", "7d": "7d"}
KINDS = ("slow", "failed")
ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
LIMIT_MS = 10_000  # the project's limit for the first text
CACHE_SECONDS = 30.0
CACHED = 50
QUERY_SECONDS = 10.0
UNAVAILABLE = "The telemetry cannot be read right now. Try again later."

_TURNS = """AppDependencies
| where TimeGenerated > ago({period}) and Name == "chat.turn"
| extend P = Properties
| project TimeGenerated, turn = tostring(P.turn_id), backend = tostring(P["voltrieve.backend"]),
    client = tostring(P["voltrieve.client"]), outcome = tostring(P["voltrieve.outcome"]),
    error = tostring(P["voltrieve.error_code"]), first_text_ms = toint(P["voltrieve.first_text_ms"]),
    duration_ms = toint(P["voltrieve.duration_ms"]), model_calls = toint(P["voltrieve.model_calls"]),
    tools = tostring(P["voltrieve.tools"]), cost = todouble(P["voltrieve.cost_sek"]),
    cold_start = tostring(P["voltrieve.cold_start"]) == "True"
"""

HEALTH = _TURNS + """
| summarize turns = count(), errors = countif(outcome == "error"),
    first_text_median_ms = percentile(first_text_ms, 50), first_text_worst_ms = max(first_text_ms),
    first_text_over_limit = countif(first_text_ms > 10000), cold_starts = countif(cold_start),
    cost_sek = round(sum(cost), 2) by backend
| order by backend asc"""

PROBLEMS = {
    "slow": _TURNS + "\n| where first_text_ms > 10000\n| order by first_text_ms desc",
    "failed": _TURNS + '\n| where outcome == "error"\n| order by TimeGenerated desc',
}
_PROBLEM_END = """
| take 20
| project at = TimeGenerated, turn, backend, client, outcome, error, first_text_ms, duration_ms, model_calls, tools, cold_start"""

EXPLAIN = """let ops = AppDependencies
| where TimeGenerated > ago(30d) and Name == "chat.turn"
| where tostring(Properties.turn_id) == "{id}" or tostring(Properties.correlation_id) == "{id}"
| distinct OperationId | take 10;
union AppDependencies, AppRequests
| where TimeGenerated > ago(30d) and OperationId in (ops)
| extend step = case(
    Name == "chat.turn", "the whole turn",
    Type == "AppRequests", "request",
    Name startswith "POST /knowledgebases/", "search with planning",
    Name startswith "POST /indexes/", "search for source titles",
    Name has "/protocols/invocations", "hosted agent",
    Name startswith "chat " or Name endswith "/openai/v1/responses", "model or agent",
    Name startswith "invoke_agent", "prompt agent",
    Name has "/conversations" or Name == "create_conversation", "conversation",
    Name has_any ("/stock", "/prices", "/orders", "/test-drives"), "dealer service",
    Name has_any ("/msi/token", "oauth2", "discovery"), "sign-in",
    Name startswith "fastapi.", "",
    "other")
| where step != ""
| project at = TimeGenerated, service = AppRoleName, step, duration_ms = toint(DurationMs), ok = Success
| order by at asc
| take 100"""


def query(text: str, **values: str) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def health_query(period: str) -> str:
    return query(HEALTH, period=_period(period))


def problems_query(kind: str, period: str) -> str:
    if kind not in KINDS:
        raise Refused("kind must be slow or failed.")
    return query(PROBLEMS[kind] + _PROBLEM_END, period=_period(period))


def explain_query(id: str) -> str:
    if not ID_PATTERN.fullmatch(id):
        raise Refused("That is not a turn id or a correlation id.")
    return query(EXPLAIN, id=id)


def _period(period: str) -> str:
    if period not in PERIODS:
        raise Refused("period must be 1h, 24h or 7d.")
    return PERIODS[period]


class TelemetryReader:
    """Runs a query and gives its rows. The same question within 30 s gets the same answer."""

    def __init__(
        self,
        workspace_id: str,
        token: Callable[[], Awaitable[str]],
        clock: Callable[[], float] = time.monotonic,
        seconds: float = QUERY_SECONDS,
    ):
        self._workspace_id, self._token, self._clock, self._seconds = workspace_id, token, clock, seconds
        self._cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()

    async def rows(self, text: str) -> list[dict]:
        if not self._workspace_id:
            raise Refused("Telemetry is not configured here.")
        kept = self._cache.get(text)
        if kept and self._clock() < kept[0]:
            return kept[1]
        try:
            async with asyncio.timeout(self._seconds):
                token = await self._token()
                async with httpx.AsyncClient(timeout=self._seconds) as http:
                    response = await http.post(
                        QUERY_URL.format(id=self._workspace_id),
                        headers={"Authorization": f"Bearer {token}"},
                        json={"query": text},
                    )
                    response.raise_for_status()
            rows = _rows(response.json())
        except Exception as exc:
            logger.warning("A telemetry query failed: %s", type(exc).__name__)
            raise Refused(UNAVAILABLE) from None
        self._cache[text] = (self._clock() + CACHE_SECONDS, rows)
        while len(self._cache) > CACHED:
            self._cache.popitem(last=False)
        return rows


def _rows(body: dict) -> list[dict]:
    if "error" in body:  # a part of the answer is no answer
        raise ValueError
    table = body["tables"][0]
    names = [c["name"] for c in table["columns"]]
    return [
        {k: None if isinstance(v, float) and not math.isfinite(v) else v for k, v in zip(names, row, strict=True)}
        for row in table["rows"]
    ]
