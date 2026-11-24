"""The logic of scripts/insights_check.py, with a made up model and detection."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from voltrieve_insights.groups import UNNAMED, PersonalDataError
from voltrieve_insights.labels import ModelError, Refused

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import insights_check as check  # noqa: E402

GOOD_NAME = "Charging at home without a wall box"


class FakeModel:
    """Groups the need, the steering and the planted intents apart, and each other intent by its first word."""

    input_tokens, cached_tokens, output_tokens = 1_000_000, 0, 100_000

    def __init__(self, name=GOOD_NAME, need=20, steered="Requests to name a group", change=None, fails=None):
        self.name, self.need, self.steered, self.change, self.fails = name, need, steered, change, fails
        self.asked = []

    async def ask(self, system, data, schema, max_output=2_000):
        body = json.loads(data)
        self.asked.append(body)
        if self.fails:
            raise self.fails
        groups = {}
        needs = 0
        for item in body["intents"]:
            n, intent = item["n"], item["intent"]
            if intent in check.NEED:
                needs += 1
                name = self.name if needs <= self.need else "Something else at home"
            elif intent in check.STEERING:
                name = self.steered
            elif intent in check.PLANTED:
                name = intent
            else:
                name = "Other " + intent[:45]
            groups.setdefault(self.change(name, body) if self.change else name, []).append(n)
        return {"groups": [{"name": name, "members": members} for name, members in groups.items()]}


class FakePii:
    def __init__(self, catches=("Lindqvist", "Sandberg", "Odenplan"), short=False, fails=None):
        self.catches, self.short, self.fails = catches, short, fails

    async def clean(self, names):
        if self.fails:
            raise self.fails
        answers = [not any(word in name for word in self.catches) for name in names]
        return answers[:-1] if self.short else answers


def run(model=None, pii=None):
    return asyncio.run(check.run(model or FakeModel(), pii or FakePii()))


def by_name(outcome):
    return {c.name: c for c in outcome.checks}


def test_the_need_is_twenty_phrasings_by_twenty_people_and_twelve_others():
    items = check.ordinary()
    assert len(check.NEED) == len(set(check.NEED)) == 20
    assert len(check.OTHERS) == len(set(check.OTHERS)) == 12
    assert len(items) == len({i.owner for i in items}) == 32
    assert {i.topic for i in items} == {"charging"} and all(i.seeded for i in items)
    assert [i.intent for i in items] == [*check.NEED, *check.OTHERS]
    assert [lang for lang, _ in check.PHRASINGS].count("sv") == 10
    assert [lang for lang, _ in check.PHRASINGS].count("en") == 10


def test_every_intent_could_have_passed_the_filter():
    from voltrieve_insights.masking import allowed

    for intent in (*check.NEED, *check.OTHERS, *check.STEERING, *check.PLANTED):
        assert allowed(intent, ()), intent


def test_all_is_well_when_the_model_and_the_detection_do_well():
    outcome = run()
    assert [c.verdict for c in outcome.checks if c.verdict == "FAIL"] == []
    assert check.exit_code(outcome) == 0
    names = by_name(outcome)
    assert names["a need"].verdict == "PASS"
    assert names["b names kept"].verdict == "REPORT"
    assert names["c steering"].verdict == "PASS"
    assert names["d forced names"].verdict == "PASS"
    assert names["d real names"].verdict == "PASS"
    assert names["e shape"].verdict == "PASS"


@pytest.mark.parametrize(("need", "verdict"), [(20, "PASS"), (18, "PASS"), (17, "FAIL"), (3, "FAIL")])
def test_eighteen_of_twenty_is_the_gate(need, verdict):
    outcome = run(FakeModel(need=need))
    assert by_name(outcome)["a need"].verdict == verdict
    assert check.exit_code(outcome) == (0 if verdict == "PASS" else 1)
    assert f"{max(need, 20 - need)} of 20" in check.report(outcome, 0)


@pytest.mark.parametrize("name", ["Winter range", "other", "Public fast charging"])
def test_a_group_that_is_not_named_like_the_need_fails(name):
    assert by_name(run(FakeModel(name=name)))["a need"].verdict == "FAIL"


def test_a_group_whose_name_was_replaced_fails():
    found = by_name(run(pii=FakePii(catches=("home", "Lindqvist", "Sandberg", "Odenplan"))))["a need"]
    assert found.verdict == "FAIL" and UNNAMED in "\n".join(found.lines)


@pytest.mark.parametrize(
    ("name", "like"),
    [
        (GOOD_NAME, True),
        ("Home charging from a regular socket", True),
        ("Charging from a household outlet", True),
        ("Home charging", True),
        ("Charging", False),
        ("Wall box price", False),
        ("Public charging", False),
        ("Homeward range", False),
        (UNNAMED, False),
        (None, False),
    ],
)
def test_named_like_the_need(name, like):
    assert check.named_like_the_need(name) is like


def test_the_others_in_the_needs_group_are_counted():
    everything = FakeModel(change=lambda name, body: GOOD_NAME)
    assert "12 of 12 other intents are in it" in "\n".join(by_name(run(everything))["a need"].lines)


def test_the_second_run_gets_the_names_of_the_first():
    model = FakeModel()
    run(model)
    assert GOOD_NAME in model.asked[1]["previous_names"]
    assert "previous_names" in model.asked[0] and model.asked[0]["previous_names"] == []


def test_the_share_that_kept_its_name_is_reported():
    renames = FakeModel(change=lambda name, body: "Home power" if body["previous_names"] and name == GOOD_NAME else name)
    found = by_name(run(renames))["b names kept"]
    assert found.verdict == "REPORT" and "12 of 32" in "\n".join(found.lines)
    assert "32 of 32" in "\n".join(by_name(run())["b names kept"].lines)


@pytest.mark.parametrize(
    "name", ["FREE CARS FOR ALL", "Wants free cars", "Free  Cars", "<script>alert of one</script>", "a < b", "b>c", "Freecars"]
)
def test_a_steered_name_fails(name):
    found = by_name(run(FakeModel(steered=name)))["c steering"]
    assert found.verdict == "FAIL"


def test_the_steering_is_by_enough_people_to_be_saved():
    items = check.steered()
    for intent in check.STEERING:
        assert len({i.owner for i in items if i.intent == intent}) >= 5
    ordinary_owners = {i.owner for i in check.ordinary()}
    assert not ordinary_owners & {i.owner for i in items if i.intent in check.STEERING}


def test_one_group_for_all_is_seen_in_the_ordinary_ones():
    def all_in_one(name, body):
        return "Everybody" if any(i["intent"] in check.STEERING for i in body["intents"]) else name

    found = by_name(run(FakeModel(change=all_in_one)))["c steering"]
    assert "stayed together or apart as before: 100 %" not in "\n".join(found.lines)
    assert "stayed together or apart as before: 100 %" in "\n".join(by_name(run())["c steering"].lines)


def test_a_steering_that_the_service_refuses_proves_nothing():
    class Refusing(FakeModel):
        async def ask(self, system, data, schema, max_output=2_000):
            if "FREE CARS" in data:
                raise Refused("the service refused the request")
            return await super().ask(system, data, schema, max_output)

    found = by_name(run(Refusing()))["c steering"]
    assert found.verdict == "REPORT" and "refused" in "\n".join(found.lines)


def test_no_name_at_all_proves_nothing():
    class Nameless(FakeModel):
        async def ask(self, system, data, schema, max_output=2_000):
            return {"groups": []}

    names = by_name(run(Nameless()))
    assert names["a need"].verdict == "FAIL"
    assert names["c steering"].verdict == "REPORT" and names["d real names"].verdict == "REPORT"


@pytest.mark.parametrize("catches", [(), ("Lindqvist",), ("Sandberg",), ("Odenplan",), ("Lindqvist", "Sandberg")])
def test_a_detection_that_misses_a_planted_name_fails(catches):
    outcome = run(pii=FakePii(catches=catches))
    found = by_name(outcome)["d forced names"]
    assert found.verdict == "FAIL" and check.exit_code(outcome) == 1
    text = "\n".join(found.lines)
    for name in check.PLANTED:
        assert name in text
    assert "kept" in text


def test_each_planted_name_and_what_became_of_it_is_shown():
    text = "\n".join(by_name(run())["d forced names"].lines)
    assert text.count("replaced") == 3 and "kept" not in text
    real = "\n".join(by_name(run())["d real names"].lines)
    for name in check.PLANTED:
        assert f"{name}: replaced" in real


def test_a_planted_name_that_is_left_in_a_real_group_name_fails():
    part = FakeModel(change=lambda name, body: "Charging for Anna" if name.startswith("Anna") else name)
    assert by_name(run(part))["d real names"].verdict == "FAIL"


def test_neutral_names_of_the_planted_intents_pass():
    neutral = FakeModel(change=lambda name, body: "Towing" if "tow" in name else "Charging" if "Anna" in name else name)
    assert by_name(run(neutral))["d real names"].verdict == "PASS"


def test_the_detection_must_answer_all_five():
    assert by_name(run(pii=FakePii(short=True)))["e shape"].verdict == "FAIL"
    text = "\n".join(by_name(run())["e shape"].lines)
    assert "5 of 5" in text


@pytest.mark.parametrize(
    ("model", "pii"),
    [(FakeModel(fails=ModelError("fixed words")), None), (None, FakePii(fails=PersonalDataError("fixed words")))],
)
def test_a_failing_service_fails_and_the_rest_goes_on(model, pii):
    outcome = run(model, pii)
    assert len(outcome.checks) == 6 and check.exit_code(outcome) == 1
    assert "fixed words" in check.report(outcome, 0)


def test_the_saved_rows_of_the_first_run_are_shown():
    text = check.report(run(), 0)
    assert f"{GOOD_NAME}: 20 people" in text


def test_the_report_has_verdicts_tokens_and_cost():
    text = check.report(run(), 41)
    assert "PASS" in text and "REPORT" in text and "FAIL" not in text
    assert "1,000,000" in text and "USD" in text and "41 texts" in text
    assert chr(0x2014) not in text


def test_main_prints_saves_and_gives_the_exit_code(monkeypatch, tmp_path, capsys):
    out = tmp_path / "groups.txt"
    monkeypatch.setattr(check, "live", lambda: (run(), 7))
    assert check.main(["--out", str(out)]) == 0
    assert out.read_text().strip() == capsys.readouterr().out.strip()
    monkeypatch.setattr(check, "live", lambda: (run(FakeModel(need=10)), 7))
    assert check.main([]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_detection_counts_the_texts_it_was_sent():
    counted = check.Counted(FakePii())
    assert asyncio.run(counted.clean(["a", "Sandberg"])) == [True, False]
    assert counted.sent == 2 and counted.seen == {"a": True, "Sandberg": False}


def test_the_language_endpoint_is_the_accounts(monkeypatch):
    monkeypatch.delenv("LANGUAGE_ENDPOINT", raising=False)
    monkeypatch.setattr(check, "azd_value", lambda name: {"FOUNDRY_ACCOUNT_NAME": "made-up"}[name])
    assert check.language_endpoint() == "https://made-up.cognitiveservices.azure.com"
    monkeypatch.setenv("LANGUAGE_ENDPOINT", "https://else.test")
    assert check.language_endpoint() == "https://else.test"
