"""The live check of the car's tools, against a fake client."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from voltrieve_client import ApiError, Turn

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("car_tools_check", ROOT / "scripts" / "car_tools_check.py")
tools = importlib.util.module_from_spec(spec)
sys.modules["car_tools_check"] = tools
spec.loader.exec_module(tools)


class FakeApi:
    """A server that does what it is told: it calls the tool the message asks for."""

    def __init__(self, skip=(), fail=None, verdict="No, two hours at most."):
        self.skip, self.fail, self.verdict = set(skip), fail, verdict
        self.turns, self.uploads = [], []

    def upload(self, data, label=""):
        self.uploads.append(len(data))
        return {"id": "a1", "kind": "photo", "status": "ready"}

    def close(self):
        pass

    def turn(self, message, conversation_id=None, client_tools=None, handlers=None, correlation_id=None,
             context=None, attachments=None, **kwargs):
        self.turns.append({"message": message, "conversation_id": conversation_id, "context": context,
                           "tools": [t["name"] for t in client_tools or []], "id": correlation_id})
        if self.fail:
            raise ApiError(*self.fail)
        result = Turn(conversation_id=conversation_id or "c1")
        calls = []
        lowered = message.lower()
        if "wipers" in lowered:
            calls = [("set_wipers", {"mode": "fast"})]
        elif "honk" in lowered:
            calls = [("honk", {"times": 1 if "once" in lowered else 2})]
        elif "speed" in lowered:
            calls = [("set_speed", {"kmh": 50})]
        elif "park the car" in lowered:
            calls = [("park", {})]
        elif "sign" in lowered:
            calls = [("get_sign_photo", {})]
        for name, arguments in calls:
            if name in self.skip:
                continue
            output = handlers[name](json.dumps(arguments))
            text = output["output"] if isinstance(output, dict) else output
            result.tool_runs.append((name, json.dumps(arguments), text))
        steps = ["attachment_used"] if any(n == "get_sign_photo" for n, _, _ in result.tool_runs) else []
        result.events = [("trace", {"step": s}) for s in steps] + [
            ("delta", {"text": self.verdict if "sign" in lowered else "Done."}), ("done", {})]
        return result


def run(api, backends=("loop",)):
    made = []
    checks = tools.run_checks(lambda backend: made.append(backend) or api, list(backends))
    return checks, made


def test_every_tool_is_checked_on_each_backend():
    checks, made = run(FakeApi(), ("prompt", "loop", "hosted"))
    assert made == ["prompt", "loop", "hosted"]
    names = [name for name, _, _ in checks]
    assert len(names) == 18
    for backend in ("prompt", "loop", "hosted"):
        for what in ("wipers", "horn", "speed", "park", "sign photo through the tool", "honk after a sign question"):
            assert f"{what} on {backend}" in names
    assert all(ok for _, ok, _ in checks), checks


def test_the_car_declares_the_five_tools_of_the_client():
    api = FakeApi()
    run(api)
    assert api.turns[0]["tools"] == ["set_speed", "set_wipers", "honk", "park", "get_sign_photo"]


def test_the_sign_photo_goes_through_the_tool_with_a_drawn_sign():
    api = FakeApi()
    run(api)
    assert len(api.uploads) == 1 and api.uploads[0] > 10_000


def test_the_honk_after_a_sign_question_continues_the_conversation():
    api = FakeApi()
    run(api)
    sign, honk = api.turns[-2], api.turns[-1]
    assert honk["conversation_id"] == "c1" and sign["conversation_id"] is None
    assert "honk" in honk["message"].lower()


def test_a_tool_that_was_not_called_fails_the_check():
    checks, _ = run(FakeApi(skip=("set_wipers",)))
    failed = [name for name, ok, _ in checks if not ok]
    assert failed == ["wipers on loop"]


def test_a_wrong_verdict_on_the_sign_fails_the_check():
    checks, _ = run(FakeApi(verdict="Yes, you can park here."))
    failed = [name for name, ok, _ in checks if not ok]
    assert failed == ["sign photo through the tool on loop"]


def test_a_backend_that_fails_is_reported_and_the_others_go_on():
    class Broken(FakeApi):
        def turn(self, *args, **kwargs):
            raise ApiError(500, "boom")

    made = {"prompt": Broken(), "loop": FakeApi()}
    checks = tools.run_checks(lambda backend: made[backend], ["prompt", "loop"])
    assert [ok for name, ok, _ in checks if name.endswith("on loop")] == [True] * 6
    assert not any(ok for name, ok, _ in checks if name.endswith("on prompt"))
    assert any("HTTP 500" in note for _, _, note in checks)


def test_the_context_has_the_clock_and_the_car():
    api = FakeApi()
    run(api)
    context = api.turns[0]["context"]
    assert context["local_time"].startswith("2026-11-24T10:00") and context["car"]["model"] == "Voltrieve Spark"


def test_the_report_has_a_line_per_check_and_a_total():
    checks, _ = run(FakeApi(skip=("honk",)))
    text = tools.report(checks)
    assert text.count("\n") >= len(checks)
    assert "FAILED" in text and "ok" in text and "6 checks" in text


def test_the_tool_list_is_the_one_of_the_car_client():
    declared = json.loads((ROOT / "clients" / "car" / "src" / "tools.json").read_text(encoding="utf-8"))
    assert tools.CAR_TOOLS == declared


def test_the_script_has_no_em_dash():
    assert "\u2014" not in (ROOT / "scripts" / "car_tools_check.py").read_text(encoding="utf-8")
