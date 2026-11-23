# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Smoke test of a deployment: is everything up and does one question get answered?

    uv run scripts/smoke.py [--backends prompt,loop,hosted] [--basic]

Checks, in order:
  1. GET /healthz on the API and on the dealer service (no sign-in needed)
  2. the search indexer's last run succeeded
  3. one short turn on each backend through the API: an answer and `done`, no `error` event
  4. the same on the prompt agent with the car's tools declared, which no other turn reaches
  5. a drawn road sign uploaded and asked about on each backend: the answer names its code

`--basic` leaves out 4 and 5: the agents are deployed before the API, and an older API has neither.
Prints one line per check and exits 1 if any failed. It needs `azd env select` (API_URL,
DEALER_URL, SEARCH_ENDPOINT, API_CLIENT_ID) and `az login` as a user with Chat.User.
scripts/deploy-agents.sh runs it with `--basic` at the end.
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voltrieve_client import Api, ApiError, Turn, api_token, api_url, azd_value, cached_token  # noqa: E402

INDEXER_NAME = "voltrieve-docs-indexer"
SEARCH_API_VERSION = "2026-11-01-preview"
QUESTION = "What is the towing capacity of the Fjord?"
SIGN_CODE = "K7Q"  # hard to guess: the answer has to come from the photo
PHOTO_QUESTION = "Which characters are on the road sign in my photo? Answer with exactly those characters."
# The names decide which prompt agent answers (CAR_AGENT_TOOL_NAMES in the API, a test checks it).
# The agent has the schemas in its definition, and no tool is needed here.
CAR_TOOLS = [
    {"name": name, "description": "", "parameters": {"type": "object", "properties": {}}}
    for name in ("set_speed", "set_wipers", "honk", "park", "get_sign_photo")
]
SIGN_SIDE = 320
GLYPH_SCALE, GLYPH_GAP = 14, 14
GLYPHS = {  # 5 by 7 pixels each
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
}
BLUE, WHITE = b"\x00\x52\x9b", b"\xff\xff\xff"

Check = tuple[str, bool, str]  # name, passed, note


def check_health(name: str, base_url: str, http: httpx.Client) -> Check:
    try:
        response = http.get(f"{base_url.rstrip('/')}/healthz", timeout=60.0)
    except httpx.HTTPError as exc:
        return name, False, type(exc).__name__
    ok = response.status_code == 200 and response.json().get("status") == "ok"
    return name, ok, f"HTTP {response.status_code}"


def check_indexer(search_endpoint: str, token: str, http: httpx.Client) -> Check:
    name = "search indexer"
    try:
        response = http.get(
            f"{search_endpoint.rstrip('/')}/indexers/{INDEXER_NAME}/status",
            params={"api-version": SEARCH_API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        return name, False, type(exc).__name__
    if response.status_code != 200:
        return name, False, f"HTTP {response.status_code}"
    last = response.json().get("lastResult") or {}
    status = last.get("status", "never ran")
    failed = last.get("itemsFailed", 0)
    return name, status == "success" and failed == 0, f"last run {status}, {failed} items failed"


def sign_png() -> bytes:
    """A drawn parking sign, the characters of SIGN_CODE in white on blue, as a PNG."""
    glyph_width, glyph_height = 5 * GLYPH_SCALE, 7 * GLYPH_SCALE
    left = (SIGN_SIDE - (len(SIGN_CODE) * glyph_width + (len(SIGN_CODE) - 1) * GLYPH_GAP)) // 2
    top = (SIGN_SIDE - glyph_height) // 2

    def is_white(x: int, y: int) -> bool:
        column, row = x - left, y - top
        if not (0 <= row < glyph_height and column >= 0):
            return False
        index, inside = divmod(column, glyph_width + GLYPH_GAP)
        if index >= len(SIGN_CODE) or inside >= glyph_width:
            return False
        return GLYPHS[SIGN_CODE[index]][row // GLYPH_SCALE][inside // GLYPH_SCALE] == "1"

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    side = range(SIGN_SIDE)
    rows = b"".join(b"\0" + b"".join(WHITE if is_white(x, y) else BLUE for x in side) for y in side)
    header = struct.pack(">IIBBBBB", SIGN_SIDE, SIGN_SIDE, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def check_answer(name: str, ask: Callable[[], Turn], problem: Callable[[Turn], str | None] = lambda _: None) -> Check:
    try:
        turn = ask()
    except ApiError as exc:
        return name, False, str(exc)[:200]
    if turn.error:
        return name, False, f"error event: {turn.error.get('code')}"
    found = problem(turn)
    if found:
        return name, False, found
    ok = turn.done is not None and bool(turn.text)
    return name, ok, f"{len(turn.text)} characters, done: {turn.done is not None}"


def check_turn(backend: str, api: Api) -> Check:
    return check_answer(f"turn on {backend}", lambda: api.turn(QUESTION, correlation_id=f"smoke-{backend}"))


def check_car(backend: str, api: Api) -> Check:
    return check_answer(
        f"car on {backend}",
        lambda: api.turn(QUESTION, client_tools=CAR_TOOLS, correlation_id=f"smoke-car-{backend}"),
        lambda turn: "called a client tool" if turn.tool_runs else None,
    )


def check_photo(backend: str, api: Api) -> Check:
    def ask() -> Turn:
        upload = api.upload(sign_png(), "road sign")
        if upload.get("status") != "ready":
            raise ApiError(201, f"the photo is {upload.get('status')}: {upload.get('code')}")
        return api.turn(PHOTO_QUESTION, attachments=[upload["id"]], correlation_id=f"smoke-photo-{backend}")

    def problem(turn: Turn) -> str | None:
        if "attachment_used" not in turn.trace_steps:
            return "the photo was not used"
        if SIGN_CODE.lower() not in turn.text.lower():
            return "the answer does not name the code on the sign: did the photo reach the model?"
        return None

    return check_answer(f"photo on {backend}", ask, problem)


def run_checks(
    api_base: str,
    dealer_base: str,
    search_endpoint: str,
    search_token: Callable[[], str],
    user_token: Callable[[], str],
    backends: list[str],
    http: httpx.Client,
    basic: bool = False,
) -> list[Check]:
    results = [
        check_health("api /healthz", api_base, http),
        check_health("dealer /healthz", dealer_base, http),
        check_indexer(search_endpoint, search_token(), http),
    ]
    for backend in backends:
        api = Api(api_base, user_token, backend)
        try:
            results.append(check_turn(backend, api))
            if not basic:
                if backend == "prompt":
                    results.append(check_car(backend, api))
                results.append(check_photo(backend, api))
        finally:
            api.close()
    return results


def search_token() -> str:
    result = subprocess.run(
        ["az", "account", "get-access-token", "--scope", "https://search.azure.com/.default",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise SystemExit("Could not get a search token from the az CLI. Run `az login`.")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backends", default="prompt,loop,hosted")
    parser.add_argument("--basic", action="store_true", help="leave out the car's tools and the photos")
    args = parser.parse_args()
    with httpx.Client() as http:
        results = run_checks(
            api_url(), azd_value("DEALER_URL"), azd_value("SEARCH_ENDPOINT"), search_token,
            cached_token(api_token), [b for b in args.backends.split(",") if b], http, args.basic,
        )
    for name, ok, note in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<18}{note}")
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
