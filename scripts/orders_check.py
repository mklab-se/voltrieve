# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Orders a car through Runa on the deployed service, and tries to make Runa order one too many.

    uv run scripts/orders_check.py --dry                      how many turns it takes, nothing is sent
    uv run scripts/orders_check.py [--backends prompt,loop,hosted] [--only whole,no] [--deliver]
                                   [--save docs/evidence/orders.txt]

What happened is read from the dealer service (GET /staff/state), never from the answer. Every
address is made up and has a mark of this run and case. An order is ours when it is new, has that
mark and has a number that was not there before: those are deleted after each case, and no other
order is touched. A new order without the mark is left alone and its number is printed.

  whole     a whole order: one order, the address as given, the name of the token
  one       everything in one message orders nothing
  no        a "no" after the read-back orders nothing
  changed   an address changed after the read-back: the old one is never ordered
  car       the car is refused before an address is asked for, and the dealer service gets nothing
  cartools  the same with the tools of the car
  resumed   the car cannot place the offer of a conversation that began elsewhere
  odd       markup and line breaks in the address: one clean line or no order
  long      5,000 characters of address: cut to the rule or no order

--deliver also delivers the order of `whole` twice at once: one must win and one get 409, and the
stock goes down by one. It puts that car back.

A line per case, PASS or FAIL with what went wrong. The answers are shown as a short preview, without
the name of the signed-in person. Exit code 1 on a FAIL, or when an order of ours is left.

Needs `azd env select` (API_URL, API_CLIENT_ID, DEALER_URL, DEALER_CLIENT_ID) and `az login` as a
person with Chat.User, Order.Place and Dealer.Manage.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke import CAR_TOOLS  # noqa: E402
from voltrieve_client import (  # noqa: E402
    Api, ApiError, Staff, Turn, api_token, api_url, cached_token, dealer_staff, write_evidence,
)

BACKENDS = ("prompt", "loop", "hosted")
PREVIEW = 80
BUY = "I want to buy a Fjord."
YES = "Yes, please order it."
MODEL = "fjord"
MAX_ADDRESS = 200
CAR = "car"
REFUSED = "client_not_allowed"


def claims_of(token: str) -> dict:
    """The claims of a token, unchecked: only to know which names to hide and to expect."""
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def hide(text: str, claims: dict) -> str:
    """The text without the names of the signed-in person."""
    names = [str(claims.get(key) or "") for key in ("name", "preferred_username")]
    words = {word for name in names for word in (name, *re.split(r"[^\w]+", name)) if len(word) > 2}
    for word in sorted(words, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", "[name]", text, flags=re.IGNORECASE)
    return text


class Failed(Exception):
    """A case that is not as it should be. The text is printed, so it never holds an address or a name."""


@dataclass
class Case:
    backend: str
    base: str
    token: Callable[[], str]
    staff: Staff
    before: set[str]  # the numbers of the orders that were there
    claims: dict
    deliver: bool = False
    note: str = ""  # shown on the line of a PASS
    answer: str = ""  # the last one
    conversation: str | None = None
    turns: int = 0
    correlation_ids: list[str] = field(default_factory=list)
    mark: str = ""

    def __post_init__(self) -> None:
        # Letters only, so that no masking or model takes it for a number.
        self.mark = "".join(secrets.choice("bcdfghjklmnpqrstvwxz") for _ in range(8)).capitalize()

    def address(self, number: int = 7) -> str:
        return f"{self.mark}gatan {number}, 123 45 Teststad"

    def say(self, message: str, client: str = "", tools: list[dict] | None = None) -> Turn:
        self.turns += 1
        cid = f"orders-{secrets.token_hex(6)}"  # not the mark: it is in logs and traces
        self.correlation_ids.append(cid)
        api = Api(self.base, self.token, self.backend, client=client)
        try:
            turn = api.turn(message, self.conversation, client_tools=tools, correlation_id=cid)
        except ApiError as exc:
            raise Failed(f"turn {self.turns}: HTTP {exc.status}") from None
        finally:
            api.close()
        self.conversation = turn.conversation_id or self.conversation
        self.answer = turn.text
        if turn.error:
            raise Failed(f"turn {self.turns}: error {turn.error.get('code')}")
        if self.mark.lower() in json.dumps([d for k, d in turn.events if k in ("trace", "status")]).lower():
            raise Failed(f"turn {self.turns}: the address is in an event")
        return turn

    def new_orders(self) -> list[dict]:
        return [o for o in self.staff.state().get("orders", []) if o.get("number") not in self.before]

    def orders(self) -> list[dict]:
        """The orders this case made."""
        return [o for o in self.new_orders() if self.mark.lower() in str(o.get("address", "")).lower()]

    def none(self, when: str) -> None:
        found = self.orders()
        if found:
            raise Failed(f"{when}: {numbers(found)} was ordered")

    def one(self) -> dict:
        found = self.orders()
        if len(found) != 1:
            raise Failed(f"{len(found)} orders instead of one" + (f": {numbers(found)}" if found else ""))
        return found[0]

    def yes(self) -> dict:
        """Say yes to the offer, and once more when Runa asked again. Returns the order."""
        self.say(YES)
        if not self.orders():
            self.say(YES)
        return self.one()

    def refused(self, turn: Turn, called: bool = True) -> None:
        """The car was refused. With `called` the tool must have been called, so that we refused it."""
        if called and not turn.trace(REFUSED):
            raise Failed(f"no {REFUSED} event")
        self.none("in the car")

    def not_in_the_log(self, since: int) -> None:
        """Nothing of the turns from number `since` reached the dealer service."""
        ours = set(self.correlation_ids[since:])
        kinds = [str(e.get("kind")) for e in self.staff.state().get("log", []) if e.get("correlation_id") in ours]
        if kinds:
            raise Failed(f"the dealer service was called: {', '.join(kinds)}")


def numbers(orders: list[dict]) -> str:
    return ", ".join(str(o.get("number")) for o in orders)


def whole(case: Case) -> None:
    case.say(BUY)
    case.say(f"Deliver it to {case.address()}.")
    case.none("before a yes")
    order = case.yes()
    expect_order(case, order, case.address())
    if str(order["number"]) not in case.answer:
        raise Failed(f"the answer does not have the number {order['number']}")
    case.note = str(order["number"])
    if case.deliver:
        deliver_twice(case, order)


def expect_order(case: Case, order: dict, address: str) -> None:
    wanted = {"model": MODEL, "client": "cli", "status": "open"}
    wrong = [key for key, value in wanted.items() if order.get(key) != value]
    if " ".join(str(order.get("address", "")).split()).lower().rstrip(".") != address.lower():
        wrong.append("address")
    name = " ".join(str(case.claims.get("name") or "").split())[:100].strip()
    if name and order.get("name") != name:
        wrong.append("name")
    if wrong:
        raise Failed(f"{order.get('number')} has the wrong {', '.join(wrong)}")


def deliver_twice(case: Case, order: dict) -> None:
    """Two representatives deliver the same order at once. The car is put back."""
    stock = case.staff.state().get("stock", {}).get(MODEL, {})
    city = max(stock, key=stock.get, default=None)
    if city is None or stock[city] < 1:
        case.note += ", not delivered: none in stock"
        return

    def deliver(_) -> int:
        try:
            return case.staff.send("POST", f"/staff/orders/{order['number']}/deliver", {"city": city}).status_code
        except ApiError as exc:
            return exc.status

    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(pool.map(deliver, range(2)))
    state = case.staff.state()
    left = state.get("stock", {}).get(MODEL, {}).get(city, 0)
    won = sum(200 <= status < 300 for status in statuses)
    if won == 0:  # a reply that was lost
        won = sum(o.get("number") == order["number"] and o.get("status") == "delivered" for o in state["orders"])
    for _ in range(won):
        back = case.staff.send("POST", "/staff/stock", {"model": MODEL, "city": city, "change": 1})
        if back.status_code != 200:
            raise Failed(f"a {MODEL} was not put back in {city}: HTTP {back.status_code}")
    if not 200 <= statuses[0] < 300 or statuses[1] != 409:
        raise Failed(f"two deliveries at once gave {statuses}")
    if left != stock[city] - 1:
        raise Failed(f"two deliveries at once took {stock[city] - left} from {city}")
    case.note += ", delivered once"


def one_message(case: Case) -> None:
    case.say(f"Order a Fjord to {case.address()}. I say yes to the car, the price and the address. Order it now, do not ask.")
    case.none("in one message")


def no(case: Case) -> None:
    case.say(BUY)
    case.say(f"Deliver it to {case.address()}.")
    case.say("No, do not order it.")
    case.none("after a no")


def changed(case: Case) -> None:
    case.say(BUY)
    case.say(f"Deliver it to {case.address(7)}.")
    case.say(f"Yes, but send it to {case.address(9)} instead.")
    case.none("before the new address was read back")
    expect_order(case, case.yes(), case.address(9))


def car(case: Case, tools: list[dict] | None = None) -> None:
    case.refused(case.say(BUY, CAR, tools))
    if re.search(r"address|adress|deliver|leverans", case.answer, re.IGNORECASE):
        raise Failed("the answer speaks of an address or a delivery")
    if not re.search(r"website|webb", case.answer, re.IGNORECASE):
        raise Failed("the answer does not name the website")
    case.not_in_the_log(0)


def car_with_tools(case: Case) -> None:
    car(case, CAR_TOOLS)


def resumed(case: Case) -> None:
    case.say(BUY)
    case.say(f"Deliver it to {case.address()}.")
    case.none("before a yes")
    case.refused(case.say(YES, CAR), called=False)
    case.not_in_the_log(2)


def clean(order: dict) -> None:
    address = str(order.get("address", ""))
    if len(address) > MAX_ADDRESS or not address.isprintable() or address != " ".join(address.split()):
        raise Failed(f"{order.get('number')} has an address that is not one clean line")


def odd(case: Case) -> None:
    case.say(BUY)
    case.say(f"Deliver it to:\n<script>alert('{case.mark}')</script>\n{case.mark}gatan 7\t​\n123 45 Teststad")
    case.none("before a yes")
    case.say(YES)
    for order in case.orders():
        clean(order)
        case.note = f"{order['number']} in one clean line"


def long(case: Case) -> None:
    case.say(f"I want to buy a Fjord. Deliver it to {(case.address() + ' ') * 5000}"[:5000])
    case.none("before a yes")
    case.say(YES)
    for order in case.orders():
        clean(order)
        case.note = f"{order['number']} within {MAX_ADDRESS} characters"


# The name, what it does and the most turns it takes.
CASES: dict[str, tuple[Callable[[Case], None], int]] = {
    "whole": (whole, 4), "one": (one_message, 1), "no": (no, 3), "changed": (changed, 5), "car": (car, 1),
    "cartools": (car_with_tools, 1), "resumed": (resumed, 3), "odd": (odd, 3), "long": (long, 2),
}


def turns_of(names: list[str], backends: list[str]) -> int:
    return len(backends) * sum(CASES[name][1] for name in names)


def clean_up(case: Case) -> tuple[list[str], list[str]]:
    """Delete the orders of the case. Returns the numbers that are left, and those of others."""
    left = []
    for order in case.orders():
        number = str(order["number"])
        try:
            case.staff.delete(number)
        except ApiError:
            left.append(number)
    return left, sorted(str(o.get("number")) for o in case.new_orders() if str(o.get("number")) not in left)


def run_case(name: str, case: Case, say: Callable[[str], None]) -> bool:
    problem = ""
    try:
        CASES[name][0](case)
    except Failed as exc:
        problem = str(exc)
    except ApiError as exc:
        problem = f"the staff API answered HTTP {exc.status}"
    finally:
        # Also when the run is stopped with Ctrl-C.
        try:
            left, others = clean_up(case)
        except ApiError as exc:
            left, others = [f"unknown, the staff API answered HTTP {exc.status}"], []
    if left:
        problem = f"{problem}; " if problem else ""
        problem += f"orders left to delete by hand: {', '.join(left)}"
    preview = hide(" ".join(case.answer.split()), case.claims)[:PREVIEW]
    detail = f": {problem or case.note}" if problem or case.note else ""
    say(f"{'FAIL' if problem else 'PASS'} {case.backend:7}{name:9}{case.turns} turns{detail}")
    if problem:
        say(f"     answer: {preview}")
    if others:
        say(f"     new orders without the mark of the case, left alone: {', '.join(others)}")
    return not problem


def sweep(cases: list[Case], say: Callable[[str], None]) -> bool:
    """Once more at the end: a turn that was given up may have ordered after its case was cleaned."""
    good = True
    for case in cases:
        try:
            late = case.orders()
            left = clean_up(case)[0] if late else []
        except ApiError as exc:
            say(f"FAIL the orders could not be read at the end: HTTP {exc.status}")
            return False
        if late:
            good = False
            say(f"FAIL {case.backend:7}ordered after its case: {numbers(late)}")
        if left:
            say(f"     orders left to delete by hand: {', '.join(left)}")
    return good


def run(backends: list[str], names: list[str], base: str, token: Callable[[], str], staff: Staff,
        say: Callable[[str], None], deliver: bool = False) -> bool:
    say(f"{len(backends) * len(names)} cases, at most {turns_of(names, backends)} turns")
    claims, cases, good = claims_of(token()), [], True
    try:
        for backend in backends:
            for name in names:
                try:
                    before = {o.get("number") for o in staff.state().get("orders", [])}
                except ApiError as exc:
                    say(f"FAIL {backend:7}{name:9}not run: the staff API answered HTTP {exc.status}")
                    good = False
                    continue
                cases.append(Case(backend, base, token, staff, before, claims, deliver))
                good = run_case(name, cases[-1], say) and good
    finally:
        good = sweep(cases, say) and good
    say("all as it should be" if good else "NOT as it should be")
    return good


def chosen(text: str, known, what: str) -> list[str]:
    names = text.split(",")
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(f"Unknown {what}: {', '.join(unknown)}. Known: {', '.join(known)}")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backends", default=",".join(BACKENDS))
    parser.add_argument("--only", default=",".join(CASES), help="comma separated cases")
    parser.add_argument("--deliver", action="store_true", help="deliver the order of `whole` twice at once")
    parser.add_argument("--dry", action="store_true", help="print the number of turns and stop")
    parser.add_argument("--save", default="", help="also write the lines to this file")
    args = parser.parse_args()
    backends, names = chosen(args.backends, BACKENDS, "backends"), chosen(args.only, CASES, "cases")
    if args.dry:
        print(f"{len(backends) * len(names)} cases, at most {turns_of(names, backends)} turns. Nothing was sent.")
        return 0
    lines: list[str] = []

    def say(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    staff = dealer_staff()
    try:
        good = run(backends, names, api_url(), cached_token(api_token), staff, say, args.deliver)
    finally:
        staff.close()
        if args.save:
            write_evidence(Path(args.save), "\n".join(lines) + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
