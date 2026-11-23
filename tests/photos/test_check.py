"""The routing check with photo questions: verdicts, articles, a lost photo, a rate limit, cost."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import answers
from voltrieve_client import Api, ApiError, Turn

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("routing_check", ROOT / "tests" / "routing" / "check.py")
check = importlib.util.module_from_spec(spec)
sys.modules["routing_check"] = check
spec.loader.exec_module(check)

INDEX = json.loads(check.INDEX.read_text(encoding="utf-8"))
USAGE = {"input_tokens": 3000, "input_tokens_details": {"cached_tokens": 1000}, "output_tokens": 200}


def answer(text, usage=USAGE, error=None) -> Turn:
    events = []
    if error:
        events.append(("error", error))
    events += [("delta", {"text": text}), ("done", {"usage": usage}), ("trace", {"step": "model_called"})]
    return Turn(events=events)


class FakeApi(Api):
    """Like the real client, and the server is a list of answers."""

    def __init__(self, turns, upload=None):
        self.turns, self.calls, self.uploads = list(turns), [], []
        self.reply = upload or {"id": "a1", "kind": "photo", "status": "ready"}

    def upload(self, data, label=""):
        self.uploads.append((len(data), label))
        return self.reply

    def turn(self, message, **kwargs):
        self.calls.append((message, kwargs))
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(check.time, "sleep", slept.append)
    return slept


def run_one(monkeypatch, question, api, **kwargs):
    monkeypatch.setattr(check, "Api", lambda *a, **k: api)
    return check.ask("http://api", lambda: "t", "loop", question, 1, INDEX, **kwargs)


SIGN = {"id": "q", "group": "sign", "photo": {"sign": "two_hours_fee"}, "context": {"when": "2026-11-24 10:00"},
        "ask": "Can I park here for 3 hours?", "expect": [], "verdict": "no"}


def test_a_photo_turn_sends_the_photo_and_the_clock(monkeypatch):
    api = FakeApi([answer("No, the limit is 2 hours.")])
    run = run_one(monkeypatch, SIGN, api)
    message, kwargs = api.calls[0]
    assert kwargs["attachments"] == ["a1"] and message == SIGN["ask"]
    assert kwargs["context"] == answers.car_context("2026-11-24 10:00")
    assert run.right and run.verdict == "no" and run.expected_verdict == "no"
    assert (run.input_tokens, run.cached_tokens, run.output_tokens) == (3000, 1000, 200)
    assert run.upload_seconds is not None and not run.waited


def test_a_wrong_verdict_is_not_right(monkeypatch):
    run = run_one(monkeypatch, SIGN, FakeApi([answer("Yes, you can park here.")]))
    assert not run.right and run.verdict == "yes"


def test_an_answer_with_no_clear_verdict_is_not_right(monkeypatch):
    run = run_one(monkeypatch, SIGN, FakeApi([answer("It depends on the sign.")]))
    assert not run.right and run.verdict is None


LAMP = {"id": "l", "group": "lamp", "photo": {"lamp": "left"}, "ask": "What is broken?", "expect": ["parts"],
        "article": {"key": "headlamp_left", "fits": "Voltrieve Spark"},
        "no_article": {"key": "headlamp_right", "fits": "Voltrieve Spark"}, "mentions": ["left"]}


def with_parts(turn):
    turn.events.insert(0, ("trace", {"step": "server_tool_called", "tool": "search_spare_parts"}))
    return turn


def test_the_right_lamp_is_named(monkeypatch):
    run = run_one(monkeypatch, LAMP, FakeApi([with_parts(answer("The left headlamp: VP-14375."))]))
    assert run.right and run.articles_found == 1 and not run.forbidden


def test_the_lamp_of_the_other_side_is_wrong(monkeypatch):
    run = run_one(monkeypatch, LAMP, FakeApi([with_parts(answer("The left headlamp: VP-14375 and VP-14440."))]))
    assert run.forbidden == ["VP-14440"] and not run.right


def test_any_article_is_forbidden_when_the_question_says_any(monkeypatch):
    question = {"id": "u", "group": "undamaged", "photo": {"car": "undamaged"}, "ask": "What is broken?",
                "expect": [], "no_article": "any"}
    fine = run_one(monkeypatch, question, FakeApi([answer("Nothing is broken that I can see.")]))
    bad = run_one(monkeypatch, question, FakeApi([answer("The headlamp, VP-14375.")]))
    assert fine.right and bad.forbidden == ["VP-14375"] and not bad.right


def test_sources_may_be_free(monkeypatch):
    question = {"id": "w", "group": "which-part", "photo": {"lamp": "left"}, "ask": "Which part is this?",
                "expect": [], "free_sources": True, "no_article": {"key": "headlamp_right", "fits": "Voltrieve Spark"}}
    run = run_one(monkeypatch, question, FakeApi([with_parts(answer("A headlamp, VP-14375."))]))
    assert run.right


def test_a_blurred_photo_must_be_rejected_at_upload(monkeypatch):
    question = {"id": "b", "group": "blurred", "photo": {"blurred": "two_hours_fee"}, "ask": "Can I park here?",
                "expect": [], "rejected": "too_blurry"}
    api = FakeApi([], upload={"id": None, "kind": "photo", "status": "rejected", "code": "too_blurry"})
    run = run_one(monkeypatch, question, api)
    assert run.right and not api.calls and run.rejected == "too_blurry"
    accepted = run_one(monkeypatch, question, FakeApi([answer("No.")]))
    assert not accepted.right


def test_a_photo_that_was_not_expected_to_fail_is_an_error(monkeypatch):
    api = FakeApi([], upload={"id": None, "kind": "photo", "status": "rejected", "code": "too_blurry"})
    run = run_one(monkeypatch, SIGN, api)
    assert not run.right and "too_blurry" in run.error


def test_a_lost_photo_is_uploaded_again_and_not_counted_wrong(monkeypatch):
    lost = ApiError(422, '{"detail":{"code":"unknown_attachment"}}')
    api = FakeApi([lost, answer("No, two hours at most.")])
    run = run_one(monkeypatch, SIGN, api)
    assert run.right and run.reuploaded and len(api.uploads) == 2


def test_a_rate_limit_waits_and_goes_on(monkeypatch, waits):
    limited = answer("", error={"code": "rate_limited", "message": "Too many requests"})
    api = FakeApi([limited, limited, answer("No.")])
    run = run_one(monkeypatch, SIGN, api, patient=True)
    assert run.right and run.waited and run.waited_seconds == sum(waits) > 0
    assert len(waits) == 2 and len(api.calls) == 3


def test_an_http_429_waits_too(monkeypatch, waits):
    api = FakeApi([ApiError(429, "Too Many Requests"), answer("No.")])
    run = run_one(monkeypatch, SIGN, api, patient=True)
    assert run.right and run.waited


def test_a_rate_limit_that_does_not_end_is_an_error(monkeypatch, waits):
    limited = answer("", error={"code": "rate_limited"})
    run = run_one(monkeypatch, SIGN, FakeApi([limited] * 10), patient=True)
    assert not run.right and run.error == "rate_limited" and len(waits) == len(check.RATE_WAITS)


def test_without_patience_a_rate_limit_is_an_error_as_before(monkeypatch, waits):
    limited = answer("", error={"code": "rate_limited"})
    run = run_one(monkeypatch, SIGN, FakeApi([limited]))
    assert run.error == "rate_limited" and not run.waited and not waits


def test_a_plain_question_is_asked_as_before(monkeypatch):
    question = {"id": "p", "group": "none", "ask": "Good morning!", "expect": []}
    api = FakeApi([answer("Good morning!")])
    run = run_one(monkeypatch, question, api)
    assert api.calls == [("Good morning!", {"correlation_id": "routing-loop-p-1"})]
    assert run.right and not api.uploads and run.expected_verdict is None


def test_the_cost_is_estimated_from_the_tokens_and_none_without_prices(monkeypatch):
    run = check.Run("loop", "q", "sign", 1, input_tokens=3000, cached_tokens=1000, output_tokens=200)
    price_in, price_cached, price_out = check.PRICES_USD_PER_MILLION[check.AGENT_MODEL]
    expected = (2000 * price_in + 1000 * price_cached + 200 * price_out) / 1e6
    assert check.cost_usd(run) == pytest.approx(expected)
    monkeypatch.setattr(check, "PRICES_USD_PER_MILLION", None)
    assert check.cost_usd(run) is None


def test_limit_takes_the_first_photo_turns():
    questions = [{"id": f"q{i}"} for i in range(5)]
    jobs = check.make_jobs(questions, ["prompt", "loop"], repeats=2, limit=3)
    assert [(b, q["id"], r) for b, q, r in jobs] == [("prompt", "q0", 1), ("loop", "q0", 1), ("prompt", "q1", 1)]
    assert len(check.make_jobs(questions, ["prompt", "loop"], repeats=2, limit=None)) == 20


def photo_runs():
    fast = check.Run("loop", "a", "sign", 1, seconds=2.0, first_text_seconds=1.5, input_tokens=3000, output_tokens=200,
                     expected_verdict="no", verdict="no", upload_seconds=0.5, photo=True, expected=[])
    slow = check.Run("loop", "b", "sign", 1, seconds=50.0, first_text_seconds=40.0, waited=True, waited_seconds=45.0,
                     expected_verdict="no", verdict="no", photo=True, expected=[])
    return [fast, slow]


def test_waited_turns_are_left_out_of_the_medians():
    text = check.report(photo_runs(), ["loop"])
    assert "Photo turns" in text
    line = next(l for l in text.splitlines() if l.startswith("loop") and "median" not in l and "waited" in l)
    assert " 2.0" in line and "50" not in line
    assert "1 waited" in line


def test_the_plain_report_has_no_photo_section():
    runs = [check.Run("loop", "p", "none", 1, seconds=1.0, expected=[])]
    assert "Photo" not in check.report(runs, ["loop"])


def test_the_photo_report_prints_tokens_and_cost():
    text = check.report(photo_runs(), ["loop"])
    assert "tokens" in text and "USD" in text and "list price" in text


def test_the_photo_report_says_it_is_not_the_bill():
    assert "not the bill" in check.report(photo_runs(), ["loop"])
