"""Save the totals of a run, and read back the group names that were saved last.

A run is one upload, so it is saved whole or not at all. Nothing here logs or prints a row, and an
error holds fixed words and at most a status. The caller's HTTP clients carry the tokens.
"""

import json
from datetime import UTC, date, datetime

import httpx

from voltrieve_insights.groups import UNNAMED
from voltrieve_insights.labels import send
from voltrieve_insights.totals import OTHER, Row

TABLE = "VoltrieveInsights_CL"
STREAM = f"Custom-{TABLE}"
INGESTION_SCOPE = "https://monitor.azure.com/.default"
INGESTION_VERSION = "2023-01-01"
QUERY_SCOPE = "https://api.loganalytics.io/.default"
QUERY_URL = "https://api.loganalytics.azure.com/v1/workspaces"
SOURCES = REAL, SEEDED = ("real", "seeded")
MAX_BODY = 1_000_000  # bytes: the service takes one megabyte in a request
TIMEOUT = 60.0  # seconds, for one try

# The same as in infra/modules/insights.bicep, where the table and the rule are.
COLUMNS = {
    "TimeGenerated": "datetime",
    "Run": "datetime",
    "WindowStart": "datetime",
    "WindowEnd": "datetime",
    "Source": "string",
    "Cut": "string",
    "Client": "string",
    "Topic": "string",
    "CarModel": "string",
    "RequestKind": "string",
    "Result": "string",
    "GroupName": "string",
    "Conversations": "int",
    "People": "int",
    "SampleShare": "real",
}


class StoreError(Exception):
    """The rows were not saved, or the names not read. The message is fixed words and at most a status."""


def _time(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def table_rows(found: list[Row], run: datetime, first: date, last: date, source: str, share: float) -> list[dict]:
    """The rows of one source as the table has them. A `run` row has null for its counts."""
    time = _time(run)
    common = {
        "TimeGenerated": time,
        "Run": time,
        "WindowStart": _day(first),
        "WindowEnd": _day(last),
        "Source": source,
        "Conversations": None,
        "People": None,
        "SampleShare": float(share),
    }
    rows = [{**common, **row} for row in found]
    if any(row.keys() != COLUMNS.keys() for row in rows):
        raise StoreError("a row does not fit the table")
    return [{name: row[name] for name in COLUMNS} for row in rows]


def _body(rows: list[dict]) -> bytes | None:
    try:
        return json.dumps(rows, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):  # ValueError covers UnicodeError
        return None


async def save(http: httpx.AsyncClient, endpoint: str, rule: str, rows: list[dict]) -> None:
    """One request and one try: a second one after a lost answer could save the run twice."""
    body = _body(rows)
    if body is None:
        raise StoreError("the rows cannot be sent")
    if len(body) >= MAX_BODY:
        raise StoreError("the rows are too large for one request")
    url = f"{endpoint.rstrip('/')}/dataCollectionRules/{rule}/streams/{STREAM}?api-version={INGESTION_VERSION}"
    status = None
    try:
        response = await http.post(url, content=body, headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        status = response.status_code
    except (httpx.HTTPError, httpx.InvalidURL):
        pass
    if status is None:
        raise StoreError("saving failed before an answer")
    if status != 204:
        raise StoreError(f"saving failed with status {status}")


def _names_query(source: str, last: date) -> str:
    """The group names of one run for this source: of the newest window up to `last` that has any, its newest
    run. Not simply the newest run: an older week can be run after a newer one."""
    return (
        f"let ins = {TABLE} | where Source == '{source}' and Cut == 'group' "
        f"and GroupName !in ('{OTHER}', '{UNNAMED}') and WindowEnd <= datetime({last.isoformat()});\n"
        "let last_end = toscalar(ins | summarize max(WindowEnd));\n"
        "let last_run_at = toscalar(ins | where WindowEnd == last_end | summarize max(Run));\n"
        "ins | where WindowEnd == last_end and Run == last_run_at | distinct Topic, GroupName"
    )


def _names(body: object) -> list[list[str]] | None:
    """Pairs of topic and name, or None when the answer is not exactly what we expect."""
    if not isinstance(body, dict) or "error" in body:  # an error next to the rows: they are only some of them
        return None
    try:
        (table,) = body["tables"]
        names, rows = [column["name"] for column in table["columns"]], table["rows"]
    except (KeyError, TypeError, ValueError):
        return None
    if names != ["Topic", "GroupName"] or not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, list) or len(row) != 2 or not all(isinstance(value, str) for value in row):
            return None
    return rows


async def previous_names(http: httpx.AsyncClient, workspace: str, source: str, last: date) -> dict[str, list[str]]:
    """The group names of each topic for one source, from before the window that ends at `last`, or from an
    earlier run of that same window."""
    if source not in SOURCES:
        raise ValueError("unknown source")
    response = await send(http, f"{QUERY_URL}/{workspace}/query", {"query": _names_query(source, last)}, {}, TIMEOUT)
    if response is None:
        raise StoreError("reading the names failed before an answer")
    if response.status_code != 200:
        raise StoreError(f"reading the names failed with status {response.status_code}")
    try:
        body = response.json()
    except (ValueError, RecursionError):
        body = None
    rows = _names(body)
    if rows is None:
        raise StoreError("the names could not be read")
    found: dict[str, list[str]] = {}
    for topic, name in rows:
        found.setdefault(topic, []).append(name)
    return found
