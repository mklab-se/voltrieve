# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Does any chat text reach telemetry? Mark a turn, then look for the mark in every table.

    uv run scripts/tracing_check.py --mark
    uv run scripts/tracing_check.py --search MARKER [MARKER ...] [--hours 4]

--mark sends one turn per backend (exit 1 unless every one ended with done). The marker is in the message and in the car's trim, so it is
in the client's line too. It prints the markers and, per backend, `done` or the error code.
--search counts, per table of the Log Analytics workspace, the rows that hold a marker. Only
counts leave the query. A hit in a table that logs queries (the search itself) is named apart and
does not count. Exit code 0 when nothing is found, 1 when anything is, 2 for a bad marker, 3 when the search failed.
Needs az and AZURE_RESOURCE_GROUP. Never put a marker in a correlation id.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys

from voltrieve_client import Api, api_token, api_url, cached_token

BACKENDS = ("loop", "prompt", "hosted")
QUERY_LOG_TABLES = {"LAQueryLogs"}  # they hold the text of our own search
QUESTION = "What is the towing capacity of the car? My reference is {marker}."


class UnexpectedAnswer(Exception):
    pass


def new_marker() -> str:
    return "vtmark" + secrets.token_hex(6)


def valid_marker(marker: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9]+", marker) is not None


def search_query(marker: str, hours: int) -> str:
    if not valid_marker(marker):
        raise ValueError("a marker is letters and digits only")
    return f'search "*{marker}*" | where TimeGenerated > ago({int(hours)}h) | summarize n=count() by $table'


def mark() -> int:
    all_done = True
    token = cached_token(api_token)
    for backend in BACKENDS:
        marker = new_marker()
        api = Api(api_url(), token, backend)
        try:
            turn = api.turn(
                QUESTION.format(marker=marker),
                context={"car": {"model": "Voltrieve Spark", "trim": f"trim-{marker}"}},
            )
            error = turn.error
            outcome = f"error {error.get('code', '?')}" if error else ("done" if turn.done else "no done")
        except Exception as exc:  # noqa: BLE001
            outcome = f"failed {type(exc).__name__}"
        finally:
            api.close()
        all_done = all_done and outcome == "done"
        print(f"{backend:<8}{marker}  {outcome}")
    return 0 if all_done else 1


def workspace_id(group: str) -> str:
    out = subprocess.run(
        ["az", "monitor", "log-analytics", "workspace", "list", "-g", group,
         "--query", "[].customerId", "-o", "json"],
        capture_output=True, text=True, check=True,
    ).stdout
    try:
        ids = json.loads(out)
    except ValueError:
        raise UnexpectedAnswer("ValueError") from None
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
        raise UnexpectedAnswer("not exactly one workspace")
    return ids[0]


def count_tables(workspace: str, marker: str, hours: int) -> dict[str, int]:
    out = subprocess.run(
        ["az", "monitor", "log-analytics", "query", "-w", workspace,
         "--analytics-query", search_query(marker, hours), "-o", "json"],
        capture_output=True, text=True, check=True,
    ).stdout
    try:
        data = json.loads(out)
        if isinstance(data, dict):  # a table response
            rows = data["tables"][0]["rows"]
            return {str(r[0]): int(r[-1]) for r in rows}
        return {str(r.get("Table") or r["$table"]): int(r["n"]) for r in data}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise UnexpectedAnswer(type(exc).__name__) from None


def search(markers: list[str], hours: int) -> int:
    workspace = workspace_id(os.environ["AZURE_RESOURCE_GROUP"])
    found = False
    for marker in markers:
        counts = count_tables(workspace, marker, hours)
        real = {t: n for t, n in counts.items() if t not in QUERY_LOG_TABLES}
        logged = {t: n for t, n in counts.items() if t in QUERY_LOG_TABLES}
        if real:
            found = True
            print(f"{marker}: " + ", ".join(f"{t} {n}" for t, n in sorted(real.items())))
        else:
            print(f"{marker}: no rows")
        for table, n in sorted(logged.items()):
            print(f"  {table} {n} (logs queries, our own search, not counted)")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mark", action="store_true")
    group.add_argument("--search", nargs="+", metavar="MARKER")
    parser.add_argument("--hours", type=int, default=4)
    args = parser.parse_args(argv)
    if args.mark:
        return mark()
    if args.hours < 1:
        print("refused: --hours is 1 or more", file=sys.stderr)
        return 2
    if not all(valid_marker(m) for m in args.search):
        print("refused: a marker is letters and digits only", file=sys.stderr)
        return 2
    try:
        return search(args.search, args.hours)
    except (subprocess.CalledProcessError, UnexpectedAnswer, KeyError, OSError) as exc:
        print(f"error: the search did not run ({type(exc).__name__}), not proof of no rows", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
