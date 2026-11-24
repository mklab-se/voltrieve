"""scripts/orders_check.py against a made up deployment, which can be wrong in one way at a time."""

import base64
import json
import re
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import orders_check  # noqa: E402
import voltrieve_client  # noqa: E402

CLIENT = httpx.Client
NAME = "Zebulon Quickford"
OTHER = {"number": "VO-1001", "model": "fjord", "price_sek": 1, "name": "Kim", "address": "Elsewhere 1",
         "client": "web", "status": "open"}
ADDRESS = re.compile(r"(?:Deliver it to|Order a Fjord to|send it to):?\s*(.+?)(?:\. I say| instead|\.?$)", re.S)


def sse(*events) -> str:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events)


def token(name=NAME) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"name": name, "preferred_username": "zq@example.com"}).encode())
    return f"h.{body.decode().rstrip('=')}.s"


class World:
    """The API, Runa and the dealer service. `wrong` names what Runa or the service gets wrong."""

    def __init__(self, wrong=""):
        self.wrong, self.orders, self.log = wrong, [dict(OTHER)], []
        self.stock = {"fjord": {"Malm\u00f6": 2, "Uppsala": 5}}
        self.deleted, self.stock_changes, self.messages, self.conversations = [], [], [], {}
        self.next = 1002

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat":
            return self.chat(request)
        assert request.headers["authorization"] == "Bearer staff"
        return self.staff(request.method, request.url.path, request)

    def place(self, address, client="cli"):
        order = {**OTHER, "number": f"VO-{self.next}", "name": NAME, "address": address, "client": client}
        self.orders.insert(0, order)
        self.next += 1
        return order["number"]

    def chat(self, request):
        assert request.headers["x-voltrieve-test"] == "1"
        body, cid = json.loads(request.content), request.headers["x-correlation-id"]
        car = request.headers.get("x-voltrieve-client") == "car" and self.wrong != "car_orders"
        text = body["message"]
        self.messages.append((text, request.headers.get("x-voltrieve-client")))
        cv = body.get("conversation_id") or f"c{len(self.messages)}"
        state = self.conversations.setdefault(cv, {})
        answer, events = self.runa(text, state, cid, car)
        done = {"conversation_id": cv, "turn_id": "t", "backend": "loop", "usage": {}}
        return httpx.Response(200, text=sse(*events, ("delta", {"text": answer}), ("done", done)))

    def runa(self, text, state, cid, car):
        if self.wrong == "error":
            return "", [("error", {"code": "model_error"})]
        if car:
            events = [] if self.wrong == "no_event" else [("trace", {"step": "client_not_allowed"})]
            if self.wrong == "car_reaches":
                self.log.append({"kind": "terms", "correlation_id": cid})
            asks = " Where shall it be delivered?" if self.wrong == "car_asks" else ""
            return f"Not in the car. See the Voltrieve website.{asks}", events
        self.log.append({"kind": "terms", "correlation_id": cid})
        found = ADDRESS.search(text)
        events = []
        if found:
            new = found[1].strip()
            if state.get("address") and self.wrong == "old_address":
                new = state["address"]
            state["address"] = new if self.wrong == "raw" else " ".join(
                "".join(c if c.isprintable() else " " for c in new).split())[: 10_000 if self.wrong == "raw" else 200]
            if self.wrong == "leak":
                events.append(("trace", {"step": "server_tool_called", "arguments": new}))
        eager = self.wrong == "eager" and found
        yes = text.startswith("Yes, please") or (self.wrong == "ignores_no" and text.startswith("No"))
        if state.get("address") and (yes or eager):
            number = self.place(state.pop("address"), "web" if self.wrong == "client" else "cli")
            if self.wrong == "twice":
                self.place(self.orders[0]["address"])
            return (f"Thank you {NAME}, " + ("it" if self.wrong == "no_number" else number) + " is ordered."), events
        return f"Shall I order it, {NAME}?", events

    def staff(self, method, path, request):
        if self.wrong == "staff_down":
            return httpx.Response(503)
        if path == "/staff/state":
            return httpx.Response(200, json={"orders": self.orders, "stock": self.stock, "log": self.log})
        if path == "/staff/stock":
            change = json.loads(request.content)
            self.stock_changes.append(change)
            self.stock[change["model"]][change["city"]] += change["change"]
            return httpx.Response(200, json={"in_stock": 1})
        number = path.split("/")[3]
        order = next(o for o in self.orders if o["number"] == number)
        if path.endswith("/deliver"):
            city = json.loads(request.content)["city"]
            if order["status"] == "delivered" and self.wrong != "delivers_twice":
                return httpx.Response(409)
            order["status"] = "delivered"
            self.stock["fjord"][city] -= 1
            return httpx.Response(200, json={})
        assert method == "DELETE"
        if self.wrong == "no_delete":
            return httpx.Response(500)
        self.deleted.append(number)
        self.orders.remove(order)
        return httpx.Response(204)


@pytest.fixture
def check(monkeypatch):
    def go(wrong="", names=tuple(orders_check.CASES), deliver=False, backends=("loop",)):
        world = World(wrong)
        transport = httpx.MockTransport(world.handle)
        monkeypatch.setattr(voltrieve_client.httpx, "Client", lambda **kw: CLIENT(**{**kw, "transport": transport}))
        staff = voltrieve_client.Staff("https://dealer", lambda: "staff")
        lines = []
        good = orders_check.run(list(backends), list(names), "https://api", token, staff, lines.append, deliver)
        return good, "\n".join(lines), world

    return go


def test_all_cases_pass_and_only_the_own_orders_are_deleted(check):
    good, text, world = check(backends=("prompt", "loop"))
    assert good and text.count("PASS") == 2 * len(orders_check.CASES) and "FAIL" not in text
    assert world.orders == [OTHER] and "VO-1001" not in world.deleted and len(world.deleted) == 2 * 4
    assert len(world.messages) <= orders_check.turns_of(list(orders_check.CASES), ["prompt", "loop"])
    assert text.startswith("18 cases, at most 46 turns")


@pytest.mark.parametrize(
    ("wrong", "name", "problem"),
    [
        ("eager", "whole", "before a yes: VO-1002 was ordered"),
        ("eager", "one", "in one message: VO-1002 was ordered"),
        ("ignores_no", "no", "after a no: VO-1002 was ordered"),
        ("old_address", "changed", "VO-1002 has the wrong address"),
        ("twice", "whole", "2 orders instead of one: VO-1003, VO-1002"),
        ("no_number", "whole", "the answer does not have the number VO-1002"),
        ("client", "whole", "VO-1002 has the wrong client"),
        ("leak", "whole", "the address is in an event"),
        ("no_event", "car", "no client_not_allowed event"),
        ("no_event", "cartools", "no client_not_allowed event"),
        ("car_asks", "car", "speaks of an address"),
        ("car_reaches", "car", "the dealer service was called: terms"),
        ("car_reaches", "resumed", "the dealer service was called: terms"),
        ("car_orders", "car", "no client_not_allowed event"),
        ("car_orders", "resumed", "in the car: VO-1002 was ordered"),
        ("raw", "odd", "not one clean line"),
        ("raw", "long", "not one clean line"),
        ("error", "one", "turn 1: error model_error"),
        ("no_delete", "whole", "orders left to delete by hand: VO-1002"),
        ("staff_down", "car", "not run: the staff API answered HTTP 503"),
    ],
)
def test_each_wrong_is_found_and_its_orders_are_deleted(check, wrong, name, problem):
    good, text, world = check(wrong, [name])
    assert not good and f"FAIL loop   {name}" in text and problem in text
    if wrong != "no_delete":
        assert world.orders == [OTHER]


def test_the_lines_never_have_the_name_or_more_than_a_preview(check):
    for wrong in ("eager", "ignores_no", "no_number", "twice", "raw", "client"):
        _, text, _ = check(wrong)
        assert "answer: " in text
        assert not re.search("zebulon|quickford|zq@", text, re.IGNORECASE) and "[name]" in text
        assert all(len(line) < 160 for line in text.splitlines())


def test_the_name_on_the_order_must_be_the_one_of_the_token(check, monkeypatch):
    monkeypatch.setattr(orders_check, "claims_of", lambda t: {"name": "Someone Else"})
    good, text, _ = check(names=["whole"])
    assert not good and "wrong name" in text and "Someone" not in text


def test_a_new_order_of_somebody_else_is_left_alone(check, monkeypatch):
    original = World.runa

    def runa(self, text, *args):
        if len(self.messages) == 1:
            self.place("Not ours 5")
        return original(self, text, *args)

    monkeypatch.setattr(World, "runa", runa)
    good, text, world = check(names=["whole"])
    assert good and "left alone: VO-1002" in text
    assert [o["number"] for o in world.orders] == ["VO-1002", "VO-1001"] and world.deleted == ["VO-1003"]


def test_an_order_that_comes_after_its_case_is_found_and_deleted(check, monkeypatch):
    first = []
    original = orders_check.run_case

    def run_case(name, case, say):
        good = original(name, case, say)
        if not first:
            first.append(case)
        else:
            WORLDS[0].place(first[0].address())
        return good

    WORLDS = []
    init = World.__init__
    monkeypatch.setattr(World, "__init__", lambda self, *a: (init(self, *a), WORLDS.append(self))[0])
    monkeypatch.setattr(orders_check, "run_case", run_case)
    good, text, world = check(names=["no", "car"])
    assert not good and "ordered after its case: VO-1002" in text and world.orders == [OTHER]


def test_the_orders_are_deleted_when_the_run_is_stopped(check, monkeypatch):
    def stopped(case):
        case.say(orders_check.BUY)
        case.say(f"Deliver it to {case.address()}.")
        case.say(orders_check.YES)
        raise KeyboardInterrupt

    worlds = []
    init = World.__init__
    monkeypatch.setattr(World, "__init__", lambda self, *a: (init(self, *a), worlds.append(self))[0])
    monkeypatch.setitem(orders_check.CASES, "whole", (stopped, 3))
    with pytest.raises(KeyboardInterrupt):
        check(names=["whole"])
    assert worlds[0].deleted == ["VO-1002"] and worlds[0].orders == [OTHER]


def test_the_deliveries_are_made_only_when_asked_and_the_car_is_put_back(check):
    good, text, world = check(names=["whole"])
    assert good and world.stock_changes == []
    good, text, world = check(names=["whole"], deliver=True)
    assert good and "delivered once" in text and world.stock == {"fjord": {"Malm\u00f6": 2, "Uppsala": 5}}
    assert world.stock_changes == [{"model": "fjord", "city": "Uppsala", "change": 1}] and world.orders == [OTHER]
    good, text, world = check("delivers_twice", names=["whole"], deliver=True)
    assert not good and "two deliveries at once gave [200, 200]" in text
    assert world.stock == {"fjord": {"Malm\u00f6": 2, "Uppsala": 5}}


def test_a_dry_run_sends_nothing(monkeypatch, capsys):
    def refuse(*args):
        raise AssertionError("nothing may be sent")

    for name in ("dealer_staff", "api_url", "api_token"):
        monkeypatch.setattr(orders_check, name, refuse)
    monkeypatch.setattr(sys, "argv", ["orders_check.py", "--dry"])
    assert orders_check.main() == 0
    assert capsys.readouterr().out == "27 cases, at most 69 turns. Nothing was sent.\n"


def test_an_unknown_case_is_refused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["orders_check.py", "--only", "whole,nope", "--dry"])
    with pytest.raises(SystemExit, match="Unknown cases: nope"):
        orders_check.main()
