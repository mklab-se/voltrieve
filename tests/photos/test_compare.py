"""The comparison of approaches, against a fake model."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("photos_compare", Path(__file__).with_name("compare.py"))
compare = importlib.util.module_from_spec(spec)
sys.modules["photos_compare"] = compare
spec.loader.exec_module(compare)


def usage(i=1000, o=50):
    return SimpleNamespace(input_tokens=i, output_tokens=o, total_tokens=i + o,
                           input_tokens_details=SimpleNamespace(cached_tokens=100))


class Call:
    def __init__(self, name, arguments, call_id="c1"):
        self.type, self.name, self.arguments, self.call_id = "function_call", name, json.dumps(arguments), call_id

    def model_dump(self, exclude_none=True):
        return {"type": self.type, "name": self.name, "arguments": self.arguments, "call_id": self.call_id}


def reply(text="", output=(), u=None):
    return SimpleNamespace(output_text=text, output=list(output), usage=u or usage())


class Model:
    """A fake Foundry client. `script(kwargs)` returns the reply, or raises."""

    def __init__(self, script):
        self.script, self.calls = script, []
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.script(kwargs)


def has_image(kwargs) -> bool:
    items = kwargs["input"]
    if isinstance(items, str):
        return False
    return any(
        part.get("type") == "input_image"
        for item in items if isinstance(item.get("content"), list)
        for part in item["content"]
    )


def verdict(v):
    return json.dumps({"verdict": v, "reason": "because"})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(compare.asyncio, "sleep", sleep)
    return slept


CASE = compare.Case("c", "two_hours_fee", "2026-11-24 10:00", "Can I stay 3 hours?", "no")


def one(setup, script, case=CASE):
    model = Model(script)
    result = asyncio.run(compare.run_one(compare.Foundry(model, "gpt-test"), "table", setup, case, 1))
    return model, result


def test_the_cases_are_drawn_signs_with_known_answers():
    cases = compare.make_cases()
    assert len(cases) == 16
    assert {c.sign for c in cases} >= {"two_hours_fee", "friday", "cleaning", "charging", "no_stopping"}
    assert sum(c.expected == "cannot_tell" for c in cases) == 1
    assert {c.expected for c in cases} == {"yes", "no", "cannot_tell"}
    assert len({c.id for c in cases}) == 16


def test_the_plan_counts_the_calls():
    plan = compare.plan(compare.make_cases(), repeats=3)
    assert len(plan) == 7 * 48
    assert compare.minimum_calls(plan) == 48 * (1 + 3 + 2) + 48 * (1 + 1 + 2 + 3)


def test_a_dry_run_prints_the_plan_and_calls_nothing(capsys):
    assert compare.main(["--dry-run", "--repeats", "2"]) == 0
    out = capsys.readouterr().out
    assert "Photo straight into the model call" in out and "A specialist tool that gets the photo" in out
    assert "7 setups" in out and "at least" in out and "calls" in out


@pytest.mark.parametrize("text,expected", [
    (verdict("yes"), "yes"), ("```json\n" + verdict("no") + "\n```", "no"), ("not json", None),
    (json.dumps({"verdict": "maybe"}), None), ("", None), (verdict("cannot_tell"), "cannot_tell"),
])
def test_parse_verdict(text, expected):
    assert compare.parse_verdict(text) == expected


def test_straight_is_one_call_with_the_photo_and_nothing_stored():
    model, result = one("straight", lambda kw: reply(verdict("no")))
    assert result.ok and result.spent.calls == 1
    assert has_image(model.calls[0]) and model.calls[0]["store"] is False and model.calls[0]["model"] == "gpt-test"
    assert "tools" not in model.calls[0]


def test_nowhere_has_no_rules_and_prompt_has_them():
    nowhere, _ = one("nowhere", lambda kw: reply(verdict("no")))
    prompt, _ = one("prompt", lambda kw: reply(verdict("no")))
    assert "Swedish parking sign" not in nowhere.calls[0]["instructions"]
    assert "Swedish parking sign" in prompt.calls[0]["instructions"]


def tool_script(name, answer):
    def script(kw):
        items = kw["input"]
        if kw.get("tools") and not any(i.get("type") == "function_call_output" for i in items):
            return reply(output=[Call(name, {"question": "q", "topic": "parking_sign"})])
        return reply(answer)

    return script


def test_the_reading_tool_keeps_the_photo_out_of_the_main_call():
    def script(kw):
        if kw.get("tools"):
            return tool_script("look_at_photo", verdict("no"))(kw)
        return reply("Tors 0-6, blue")  # the reader

    model, result = one("reading_tool", script)
    assert result.ok and result.spent.calls == 3
    main_calls = [c for c in model.calls if c.get("tools")]
    reader = [c for c in model.calls if not c.get("tools")]
    assert len(main_calls) == 2 and not any(has_image(c) for c in main_calls)
    assert len(reader) == 1 and has_image(reader[0])
    assert all(c["store"] is False for c in model.calls)


def test_the_form_reads_with_a_schema_then_answers_without_the_photo():
    def script(kw):
        if "text" in kw:
            assert kw["text"]["format"]["type"] == "json_schema"
            return reply(json.dumps({"main_sign": "parking", "panels": [], "readable": "yes"}))
        return reply(verdict("no"))

    model, result = one("form", script)
    assert result.ok and result.spent.calls == 2
    assert has_image(model.calls[0]) and not has_image(model.calls[1])
    assert "parking" in model.calls[1]["input"]


def test_the_guide_tool_returns_the_rules_and_the_photo_stays_in_the_call():
    model, result = one("guide_tool", tool_script("get_guide", verdict("no")))
    assert result.ok and result.spent.calls == 2
    assert all(has_image(c) for c in model.calls)
    output = next(i for i in model.calls[1]["input"] if i.get("type") == "function_call_output")
    assert "Swedish parking sign" in output["output"]
    assert "Swedish parking sign" not in model.calls[0]["instructions"]


def test_the_specialist_tool_gets_the_photo_and_the_main_call_does_not():
    def script(kw):
        if kw.get("tools"):
            return tool_script("analyze_parking_sign", verdict("no"))(kw)
        return reply("The two hour limit applies.")

    model, result = one("specialist_tool", script)
    assert result.ok and result.spent.calls == 3
    assert not any(has_image(c) for c in model.calls if c.get("tools"))
    assert has_image(next(c for c in model.calls if not c.get("tools")))


def test_the_tokens_of_every_call_are_added():
    def script(kw):
        if "text" in kw:
            return reply(json.dumps({"main_sign": "x", "panels": [], "readable": "yes"}), u=usage(1000, 50))
        return reply(verdict("no"), u=usage(1000, 50))

    _, result = one("form", script)
    spent = result.spent
    assert (spent.input_tokens, spent.cached_tokens, spent.output_tokens) == (2000, 200, 100)
    assert spent.total == 2100


def test_a_wrong_or_unreadable_answer_is_a_miss():
    assert not one("straight", lambda kw: reply(verdict("yes")))[1].ok
    assert not one("straight", lambda kw: reply("I do not know"))[1].ok


class RateLimitError(Exception):
    """Like openai.RateLimitError: a 429 with headers."""

    def __init__(self, retry_after=None):
        super().__init__("Error code: 429")
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


def test_a_rate_limit_waits_goes_on_and_is_marked(no_sleep):
    seen = []

    def script(kw):
        seen.append(1)
        if len(seen) < 3:
            raise RateLimitError()
        return reply(verdict("no"))

    _, result = one("straight", script)
    assert result.ok and result.waited and len(no_sleep) == 2 and result.spent.calls == 1


def test_the_wait_follows_retry_after(no_sleep):
    seen = []

    def script(kw):
        seen.append(1)
        if len(seen) == 1:
            raise RateLimitError("7")
        return reply(verdict("no"))

    one("straight", script)
    assert no_sleep == [7.0]


def test_a_text_that_only_says_429_is_not_a_rate_limit(no_sleep):
    def script(kw):
        raise RuntimeError("429 in the text of another error")

    _, result = one("straight", script)
    assert not result.ok and not no_sleep


def test_a_rate_limit_that_does_not_end_is_an_error(no_sleep):
    def script(kw):
        raise RateLimitError()

    _, result = one("straight", script)
    assert not result.ok and "RateLimitError" in result.error and len(no_sleep) == len(compare.RATE_WAITS)


def test_another_error_is_recorded_without_waiting(no_sleep):
    def script(kw):
        raise RuntimeError("the deployment does not exist")

    _, result = one("straight", script)
    assert not result.ok and "deployment" in result.error and not no_sleep


def results():
    def made(case, ok, seconds, tokens, waited=False):
        return compare.Result("approach", "straight", case, 1, "no" if ok else "yes", ok, seconds,
                              compare.Spent(tokens, 0, 100, 1), waited, "")

    return [made("a", True, 2.0, 1000), made("b", True, 60.0, 1000, waited=True), made("c", False, 4.0, 3000)]


def test_the_report_leaves_waited_runs_out_of_the_median_and_says_so():
    text = compare.report(results(), repeats=1, model="gpt-6.1-terra", cases=3)
    row = next(line for line in text.splitlines() if line.startswith("| Photo straight"))
    assert "2 of 3" in row and "3.0 s" in row  # the median of 2.0 and 4.0
    assert "1 waited" in text


def test_the_report_has_both_tables_tokens_and_misses():
    text = compare.report(results(), repeats=1, model="gpt-6.1-terra", cases=3)
    assert "| Approach |" in text and "| Where the parking rules live |" in text
    assert "tokens per run" in text.lower() and "Misses" in text and "USD" in text
    assert "\u2014" not in text


def test_without_a_price_only_tokens_are_printed(monkeypatch):
    monkeypatch.setattr(compare, "PRICES_USD_PER_MILLION", None)
    text = compare.report(results(), repeats=1, model="gpt-6.1-terra", cases=3)
    assert "USD" not in text and "no price" in text
