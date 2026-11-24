"""What people ask about: reads the nightly job's totals from Log Analytics for the dashboard.

The API may read only the insights table. The source (`real` or `seeded`) is chosen from a fixed
pair by our own code, never copied from the request into the query. Group names come from chats,
so they are data: they are never logged. Only counts are logged.
"""

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

SOURCES = ("real", "seeded")
QUERY_URL = "https://api.loganalytics.azure.com/v1/workspaces/{id}/query"
SCOPE = "https://api.loganalytics.io/.default"
TABLE = "VoltrieveInsights_CL"
CACHE_SECONDS = 300.0
ROW_LIMIT = 2000
MAX_AGE_DAYS = 400
WEEKS = 12
TIMEOUT_SECONDS = 20.0
FETCH_SECONDS = 30.0
UNAVAILABLE = "The usage insights are not available right now"
NOT_CONFIGURED = "Usage insights are not configured"

_PROJECT = "Run, WindowStart, WindowEnd, Cut, Client, Topic, CarModel, RequestKind, Result, GroupName, Conversations, People, SampleShare"
COLUMNS = tuple(c.strip() for c in _PROJECT.split(","))


class UsageUnavailable(Exception):
    """The query failed or its answer was not what we expect. The message is fixed text."""


class UsageNotConfigured(Exception):
    pass


def _rows_query(source: str) -> str:
    """The newest window, and its newest run. Not simply the newest run: an older week can be run after a newer one."""
    return (
        f'let ins = {TABLE} | where TimeGenerated > ago({MAX_AGE_DAYS}d) | where Source == "{source}";\n'
        "let last_end = toscalar(ins | summarize max(WindowEnd));\n"
        "let last_run_at = toscalar(ins | where WindowEnd == last_end | summarize max(Run));\n"
        f"ins | where WindowEnd == last_end and Run == last_run_at | project {_PROJECT} | take {ROW_LIMIT + 1}"
    )


def _weeks_query(source: str) -> str:
    return (
        f'let ends = {TABLE} | where TimeGenerated > ago({MAX_AGE_DAYS}d) | where Source == "{source}" '
        f"| where WindowEnd > ago({WEEKS * 7}d) | summarize Run = max(Run) by WindowEnd;\n"
        f'{TABLE} | where TimeGenerated > ago({MAX_AGE_DAYS}d) | where Source == "{source}" '
        f'| where Cut == "topic" | join kind=inner ends on WindowEnd, Run '
        f"| project {_PROJECT} | take {ROW_LIMIT + 1}"
    )


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def _table(body) -> list[dict]:
    """The rows of the one table in a Log Analytics answer, as dicts by column name."""
    try:
        if not isinstance(body, dict) or "error" in body:
            raise ValueError
        table = body["tables"][0]
        names = [c["name"] for c in table["columns"]]
        rows = table["rows"]
        if not all(name in names for name in COLUMNS) or not isinstance(rows, list) or len(rows) > ROW_LIMIT:
            raise ValueError
        return [dict(zip(names, row, strict=True)) for row in rows]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise UsageUnavailable(UNAVAILABLE) from exc


def build_answer(source: str, rows_body, weeks_body) -> dict:
    rows = _table(rows_body)
    run_row = next((r for r in rows if r["Cut"] == "run"), None)
    if rows and run_row is None:
        raise UsageUnavailable(UNAVAILABLE)
    first = run_row or (rows[0] if rows else None)
    out_rows = []
    for r in rows:
        conversations = _count(r["Conversations"])
        if r["Cut"] == "run" or conversations is None:
            continue
        out_rows.append(
            {
                "cut": _text(r["Cut"]),
                "client": _text(r["Client"]),
                "topic": _text(r["Topic"]),
                "car_model": _text(r["CarModel"]),
                "request_kind": _text(r["RequestKind"]),
                "result": _text(r["Result"]),
                "group": _text(r["GroupName"]),
                "conversations": conversations,
                "people": _count(r["People"]),
            }
        )
    weeks = []
    for r in _table(weeks_body):
        conversations = _count(r["Conversations"])
        if r["Cut"] != "topic" or conversations is None or not r["WindowEnd"]:
            continue
        weeks.append(
            {
                "end": str(r["WindowEnd"]),
                "topic": _text(r["Topic"]),
                "conversations": conversations,
                "people": _count(r["People"]),
            }
        )
    weeks.sort(key=lambda w: (w["end"], w["topic"]))
    share = run_row["SampleShare"] if run_row else None
    return {
        "source": source,
        "run": str(first["Run"]) if first and first["Run"] else None,
        "window": (
            {"start": str(first["WindowStart"]), "end": str(first["WindowEnd"])}
            if first and first["WindowStart"] and first["WindowEnd"]
            else None
        ),
        "sample_share": float(share) if isinstance(share, (int, float)) and not isinstance(share, bool) and math.isfinite(share) else None,
        "rows": out_rows,
        "weeks": weeks,
    }


class UsageService:
    def __init__(
        self,
        workspace_id: str,
        token: Callable[[], Awaitable[str]],
        clock: Callable[[], float] = time.monotonic,
        timeout: float = TIMEOUT_SECONDS,
    ):
        self._workspace_id = workspace_id
        self._token = token
        self._clock = clock
        self._timeout = timeout
        self._cache: dict[str, tuple[float, dict]] = {}
        self._locks = {source: asyncio.Lock() for source in SOURCES}

    @property
    def configured(self) -> bool:
        return bool(self._workspace_id)

    async def _query(self, http: httpx.AsyncClient, token: str, kql: str):
        response = await http.post(
            QUERY_URL.format(id=self._workspace_id),
            headers={"Authorization": f"Bearer {token}"},
            json={"query": kql},
        )
        response.raise_for_status()
        return response.json()

    async def _fetch(self, source: str) -> dict:
        try:
            async with asyncio.timeout(FETCH_SECONDS):
                token = await self._token()
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    rows_body = await self._query(http, token, _rows_query(source))
                    weeks_body = await self._query(http, token, _weeks_query(source))
            answer = build_answer(source, rows_body, weeks_body)
        except Exception as exc:
            logger.warning("Usage insights query failed: %s", type(exc).__name__)
            raise UsageUnavailable(UNAVAILABLE) from exc
        logger.info("Usage insights read: %d rows, %d weeks", len(answer["rows"]), len(answer["weeks"]))
        return answer

    async def insights(self, source: str) -> dict:
        if source not in SOURCES:
            raise ValueError("unknown source")
        if not self.configured:
            raise UsageNotConfigured(NOT_CONFIGURED)
        cached = self._cache.get(source)
        if cached and self._clock() < cached[0]:
            return cached[1]
        async with self._locks[source]:
            cached = self._cache.get(source)
            if cached and self._clock() < cached[0]:
                return cached[1]
            answer = await self._fetch(source)
            self._cache[source] = (self._clock() + CACHE_SECONDS, answer)
            return answer


def build_usage_service(settings) -> UsageService:
    credential = None

    async def token() -> str:
        nonlocal credential
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        return (await asyncio.to_thread(credential.get_token, SCOPE)).token

    return UsageService(settings.log_analytics_workspace_id, token)
