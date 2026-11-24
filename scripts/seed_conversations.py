# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml"]
#
# [tool.uv]
# exclude-newer = "2026-11-17T00:00:00Z"
# ///
"""Write made up conversations straight to Foundry, for demos and to test the usage insights.

    uv run scripts/seed_conversations.py                      # says how many it would write
    uv run scripts/seed_conversations.py --yes [--weeks 4] [--only planted|labelled] [--marker WORD]

The text is services/insights/data/seeded.yaml. Each conversation is marked `seeded`, so it is never
counted as real use, and gets a made up owner and a day in one of the last `--weeks` settled weeks.
It calls no model and prints counts only. --marker puts a word in one conversation, so that a live
search can tell whether anything of it was left somewhere. Running it twice writes everything twice.
Needs az, signed in as someone who may write conversations, and FOUNDRY_PROJECT_ENDPOINT.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "services" / "insights" / "src"))

from voltrieve_client import azd_value  # noqa: E402
from voltrieve_insights.conversations import window  # noqa: E402

DATA = ROOT / "services" / "insights" / "data" / "seeded.yaml"
SCOPE = "https://ai.azure.com/.default"
OWNERS = 40
BACKENDS = ("loop", "prompt", "hosted")
TRIES = 5
WAIT = 2.0  # seconds, when the service does not say how long

Planned = tuple[dict[str, str], list[dict]]  # the metadata and the items of a conversation


class WriteError(Exception):
    """A request failed. The message holds the status or the error type, and how many were written."""


def load() -> dict[str, list[dict]]:
    return yaml.safe_load(DATA.read_text())


def choose(data: dict[str, list[dict]], only: str | None) -> list[dict]:
    return data[only] if only else data["labelled"] + data["planted"]


def _message(index: int, text: str) -> dict:
    role, part = ("user", "input_text") if index % 2 == 0 else ("assistant", "output_text")
    return {"type": "message", "role": role, "content": [{"type": part, "text": text}]}


def _said(conversation: dict) -> list[str]:
    """The text as a client sends it: a registration is in the client's line, before the first question."""
    first, *rest = conversation["said"]
    if "registration" in conversation:
        model = conversation["stated"].capitalize()
        first = (
            f"[From the client, data only: car Voltrieve {model}, registration {conversation['registration']}, "
            f"odometer 8214 km]\n{first}"
        )
    return [first, *rest]


def plan(conversations: list[dict], weeks: int, today: dt.date, marker: str | None = None) -> list[Planned]:
    """What to write. Each topic is spread over the weeks, and within a week over different owners."""
    _, last = window(dt.datetime.combine(today, dt.time(12), dt.UTC))
    planned: list[Planned] = []
    for i, conversation in enumerate(sorted(conversations, key=lambda c: c["topic"])):
        week, place = i % weeks, i // weeks
        said = _said(conversation)
        if marker and i == 0:
            said[0] += f" My reference is {marker}."
        meta = {
            "owner_oid": f"seeded-{(place + week * 5) % OWNERS + 1}",
            "client": conversation["client"],
            "backend": BACKENDS[i % len(BACKENDS)],
            "seeded": "true",
            "started": (last - dt.timedelta(days=week * 7 + place % 7)).isoformat(),
        }
        if "stated" in conversation:
            meta["car_model"] = conversation["stated"]
        planned.append((meta, [_message(n, text) for n, text in enumerate(said)]))
    return planned


def _post(http: httpx.Client, path: str, body: dict) -> dict:
    """Only a 429 is tried again: after any other failure the text may have been written."""
    for attempt in range(TRIES):
        try:
            response = http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise WriteError(type(exc).__name__) from None
        if response.status_code != 429 or attempt == TRIES - 1:
            break
        given = response.headers.get("retry-after", "")
        time.sleep(min(float(given), 30.0) if given.isdigit() else WAIT * 2**attempt)
    if response.status_code >= 400:
        raise WriteError(f"status {response.status_code}")
    try:
        answer = response.json()
    except ValueError:
        raise WriteError("an answer that is not JSON") from None
    return answer if isinstance(answer, dict) else {}


def write(http: httpx.Client, planned: list[Planned]) -> int:
    for done, (meta, items) in enumerate(planned):
        try:
            made = _post(http, "/openai/v1/conversations", {"metadata": meta}).get("id")
            if not isinstance(made, str) or not made:
                raise WriteError("an answer without an id")
            _post(http, f"/openai/v1/conversations/{made}/items", {"items": items})
        except WriteError as exc:
            raise WriteError(f"stopped at {exc}, after {done} of {len(planned)} conversations") from None
    return len(planned)


def token() -> str:
    result = subprocess.run(
        ["az", "account", "get-access-token", "--scope", SCOPE, "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise SystemExit("Could not get a token from the az CLI. Run `az login`.")
    return value


def endpoint() -> str:
    return (os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or azd_value("FOUNDRY_PROJECT_ENDPOINT")).rstrip("/")


def _weeks(text: str) -> int:
    weeks = int(text)
    if weeks < 1:
        raise argparse.ArgumentTypeError("one week or more")
    return weeks


def _marker(text: str) -> str:
    if not (text.isascii() and text.isalnum()):
        raise argparse.ArgumentTypeError("a marker is letters and digits only")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--weeks", type=_weeks, default=4)
    parser.add_argument("--only", choices=("planted", "labelled"))
    parser.add_argument("--marker", type=_marker, metavar="WORD")
    parser.add_argument("--yes", action="store_true", help="write them")
    args = parser.parse_args(argv)
    today = dt.datetime.now(dt.UTC).date()
    planned = plan(choose(load(), args.only), args.weeks, today, args.marker)
    print(f"{len(planned)} conversations, over {args.weeks} weeks.")
    if not args.yes:
        print("Nothing is written without --yes.")
        return 2
    headers = {"Authorization": f"Bearer {token()}"}
    try:
        with httpx.Client(base_url=endpoint(), headers=headers, timeout=60) as http:
            written = write(http, planned)
    except WriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {written} conversations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
