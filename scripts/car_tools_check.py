# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow"]
# ///
"""Live check of the car's five tools on each backend.

    uv run scripts/car_tools_check.py [--backends prompt,loop,hosted] [--no-save]

Per backend it sends typed messages with the car's tools declared and a pretend car on this side
(the car client does the same, in a browser):

  wipers    "Turn the wipers on fast."   the model calls set_wipers with fast
  horn      "Honk the horn twice."       honk with 2
  speed     "Set the speed to 50 km/h."  set_speed with 50
  park      "Park the car."              park
  sign      a typed question about a drawn parking sign, no photo attached: the model calls
            get_sign_photo, the photo is uploaded and goes back with the tool result, and the answer
            has the right yes or no for the sign and the clock
  honk      "Honk once." in the conversation of the sign question: an ordinary turn (the photo is not
            used again) and the conversation goes on

That is 6 turns per backend. The wipers, horn, speed and park turns pause once for the tool result
(2 requests each), the sign turn pauses once and uploads one photo (3 requests), and the last turn
pauses once, so 6 turns make 13 requests. Writes docs/evidence/car-tools.txt unless --no-save.
Exits 1 when a check fails. Needs `azd env select` and `az login` with Chat.User. A drawn sign only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "photos"))

import signs  # noqa: E402
from answers import car_context, read_verdict  # noqa: E402
from voltrieve_client import Api, ApiError, Turn, api_token, api_url, cached_token, write_evidence  # noqa: E402

EVIDENCE = ROOT / "docs" / "evidence" / "car-tools.txt"
# The tools the car client declares: the same file the client builds its list from.
CAR_TOOLS = json.loads((ROOT / "clients" / "car" / "src" / "tools.json").read_text(encoding="utf-8"))
WHEN = "2026-11-24 10:00"
SIGN = "two_hours_fee"
STAY_MINUTES = 180
SIGN_QUESTION = f"Can I park here for {STAY_MINUTES // 60} hours? Please look at the sign."

Check = tuple[str, bool, str]  # name, passed, note


class PretendCar:
    """What the car client does with the tools, kept as plain state to look at afterwards."""

    def __init__(self, api) -> None:
        self.api = api
        self.speed, self.wipers, self.honks, self.parked = 0, "off", [], 0
        self.photos: list[str] = []

    def handlers(self) -> dict[str, Callable[[str], str | dict]]:
        return {
            "set_speed": self.set_speed, "set_wipers": self.set_wipers, "honk": self.honk,
            "park": self.park, "get_sign_photo": self.get_sign_photo,
        }

    def set_speed(self, arguments: str) -> str:
        self.speed = int(json.loads(arguments or "{}")["kmh"])
        return f"The car is accelerating to {self.speed} km/h."

    def set_wipers(self, arguments: str) -> str:
        self.wipers = json.loads(arguments or "{}")["mode"]
        return f"The wipers are {self.wipers}."

    def honk(self, arguments: str) -> str:
        times = int(json.loads(arguments or "{}")["times"])
        self.honks.append(times)
        return f"Honked {times} time(s)."

    def park(self, arguments: str) -> str:
        self.parked += 1
        return "Parked."

    def get_sign_photo(self, arguments: str) -> str | dict:
        upload = self.api.upload(signs.draw(SIGN), "parking sign")
        if upload.get("status") != "ready":
            return f"The photo could not be used: {upload.get('code')}."
        self.photos.append(upload["id"])
        return {"output": "The photo is attached.", "attachments": [upload["id"]]}


def called(turn: Turn, name: str) -> bool:
    return any(run[0] == name for run in turn.tool_runs)


def verdict_for_sign() -> str:
    when = dt.datetime.strptime(WHEN, "%Y-%m-%d %H:%M")
    return "yes" if signs.allowed(SIGN, when, STAY_MINUTES) else "no"


def checks_for(backend: str, api) -> list[Check]:
    car = PretendCar(api)
    context = car_context(WHEN)

    def ask(message: str, conversation_id: str | None = None) -> Turn:
        return api.turn(message, conversation_id=conversation_id, client_tools=CAR_TOOLS, handlers=car.handlers(),
                        context=context, correlation_id=f"car-tools-{backend}-{len(message)}")

    def tool_check(name: str, message: str, tool: str, done: Callable[[], bool], what: str) -> Check:
        title = f"{name} on {backend}"
        try:
            turn = ask(message)
        except ApiError as exc:
            return title, False, str(exc)
        if turn.error:
            return title, False, f"error event {turn.error.get('code')}"
        if not called(turn, tool):
            return title, False, f"{tool} was not called"
        return (title, True, what) if done() else (title, False, f"{tool} ran with other arguments")

    results = [
        tool_check("wipers", "Turn the wipers on fast.", "set_wipers", lambda: car.wipers == "fast", "wipers fast"),
        tool_check("horn", "Honk the horn twice.", "honk", lambda: car.honks == [2], "honked 2 times"),
        tool_check("speed", "Set the speed to 50 km/h.", "set_speed", lambda: car.speed == 50, "speed 50 km/h"),
        tool_check("park", "Park the car.", "park", lambda: car.parked == 1, "parked"),
    ]
    sign, conversation = check_sign(backend, ask, car)
    results.append(sign)
    results.append(check_honk(backend, ask, car, conversation))
    return results


def check_sign(backend: str, ask, car: PretendCar) -> tuple[Check, str | None]:
    title = f"sign photo through the tool on {backend}"
    try:
        turn = ask(SIGN_QUESTION)
    except ApiError as exc:
        return (title, False, str(exc)), None
    expected = verdict_for_sign()
    if turn.error:
        return (title, False, f"error event {turn.error.get('code')}"), turn.conversation_id
    if not called(turn, "get_sign_photo") or not car.photos:
        return (title, False, "get_sign_photo was not called or no photo was uploaded"), turn.conversation_id
    if "attachment_used" not in turn.trace_steps:
        return (title, False, "the photo did not reach the model"), turn.conversation_id
    got = read_verdict(turn.text)
    if got != expected:
        return (title, False, f"the answer said {got}, the sign says {expected}"), turn.conversation_id
    return (title, True, f"{SIGN}, {WHEN}: {expected}"), turn.conversation_id


def check_honk(backend: str, ask, car: PretendCar, conversation: str | None) -> Check:
    title = f"honk after a sign question on {backend}"
    if not conversation:
        return title, False, "the sign question gave no conversation to go on in"
    before = len(car.honks)
    try:
        turn = ask("Honk once.", conversation)
    except ApiError as exc:
        return title, False, str(exc)
    if turn.error:
        return title, False, f"error event {turn.error.get('code')}"
    if turn.conversation_id != conversation:
        return title, False, "the answer came in another conversation"
    if "attachment_used" in turn.trace_steps:
        return title, False, "the old photo was used again"
    if not called(turn, "honk") or car.honks[before:] != [1]:
        return title, False, "honk was not called once"
    return title, True, "an ordinary turn, in the same conversation"


def run_checks(make_api: Callable[[str], object], backends: list[str]) -> list[Check]:
    checks: list[Check] = []
    for backend in backends:
        api = make_api(backend)
        try:
            checks += checks_for(backend, api)
        finally:
            api.close()
    return checks


def report(checks: list[Check]) -> str:
    lines = [f"Car tools, {dt.date.today().isoformat()}. A drawn sign ({SIGN}) and a pretend car; no real photos."]
    lines += [f"{'ok' if ok else 'FAILED':7}{name}: {note}" for name, ok, note in checks]
    failed = sum(not ok for _, ok, _ in checks)
    lines.append(f"{len(checks)} checks, {failed} failed")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backends", default="prompt,loop,hosted")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    url, token = api_url(), cached_token(api_token)
    checks = run_checks(lambda backend: Api(url, token, backend), args.backends.split(","))
    text = report(checks)
    print(text)
    if not args.no_save:
        write_evidence(EVIDENCE, text)
    return 1 if any(not ok for _, ok, _ in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
