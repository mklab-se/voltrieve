"""The photo questions have right answers: the rules agree with every verdict."""

import collections
import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

import signs

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = yaml.safe_load((Path(__file__).with_name("questions.yaml")).read_text(encoding="utf-8"))["questions"]
INDEX = json.loads((ROOT / "data" / "generated" / "parts-index.json").read_text(encoding="utf-8"))
SIGN_QUESTIONS = [q for q in QUESTIONS if "rule" in q]


def when(question: dict) -> dt.datetime:
    return dt.datetime.strptime(question["context"]["when"], "%Y-%m-%d %H:%M")


def test_the_ids_are_unique():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))


def test_there_are_enough_sign_questions_over_all_eight_signs():
    assert len(SIGN_QUESTIONS) >= 24
    assert {q["photo"]["sign"] for q in SIGN_QUESTIONS} == set(signs.SIGN_IDS)


def test_yes_and_no_are_balanced():
    count = collections.Counter(q["verdict"] for q in SIGN_QUESTIONS)
    assert set(count) == {"yes", "no"}
    assert abs(count["yes"] - count["no"]) <= 2


def test_there_are_fee_questions_and_both_answers():
    fees = [q for q in SIGN_QUESTIONS if "fee" in q["rule"]]
    assert len(fees) >= 6
    assert {q["verdict"] for q in fees} == {"yes", "no"}


@pytest.mark.parametrize("question", SIGN_QUESTIONS, ids=lambda q: q["id"])
def test_the_verdict_follows_the_rules(question):
    rule, sign = question["rule"], question["photo"]["sign"]
    if "fee" in rule:
        known = signs.fee(sign, when(question))
    else:
        known = signs.allowed(sign, when(question), rule["minutes"], charging=rule.get("charging", False))
    assert ("yes" if known else "no") == question["verdict"]


@pytest.mark.parametrize("question", SIGN_QUESTIONS, ids=lambda q: q["id"])
def test_a_sign_question_has_a_clock_and_no_sources(question):
    assert question["expect"] == [] and question["group"] in ("sign", "fee")
    assert "when" in question["context"]


def test_every_photo_is_one_that_can_be_drawn():
    for question in QUESTIONS:
        photo = question["photo"]
        assert len(photo) == 1
        kind, value = next(iter(photo.items()))
        assert kind in ("sign", "lamp", "car", "blurred")
        if kind in ("sign", "blurred"):
            assert value in signs.SIGN_IDS
        if kind == "lamp":
            assert value in ("left", "right")
        if kind == "car":
            assert value == "undamaged"


def parts(key: str) -> set[str]:
    return {p["article"] for p in INDEX["parts"] if p["key"] == key and "Voltrieve Spark" in p["fits"]}


def test_the_broken_lamps_name_the_part_and_the_side():
    lamps = {q["photo"]["lamp"]: q for q in QUESTIONS if q["id"].startswith("lamp-") and "article" in q}
    assert set(lamps) == {"left", "right"}
    assert parts(lamps["left"]["article"]["key"]) == {"VP-14375"}
    assert parts(lamps["right"]["article"]["key"]) == {"VP-14440"}
    assert lamps["left"]["no_article"]["key"] == lamps["right"]["article"]["key"]
    assert lamps["right"]["no_article"]["key"] == lamps["left"]["article"]["key"]


def test_the_other_cases_are_there():
    by_id = {q["id"]: q for q in QUESTIONS}
    assert any(q["photo"].get("car") == "undamaged" and q.get("no_article") == "any" for q in QUESTIONS)
    assert any("sign" in q["photo"] and q.get("no_article") == "any" and q["group"] == "broken-part" for q in QUESTIONS)
    assert any(q["group"] == "which-part" and "lamp" in q["photo"] for q in QUESTIONS)
    assert any(q["group"] == "car-parking" and q["photo"].get("car") == "undamaged" for q in QUESTIONS)
    blurred = [q for q in QUESTIONS if "blurred" in q["photo"]]
    assert len(blurred) == 1 and blurred[0]["rejected"] == "too_blurry"
    assert by_id[blurred[0]["id"]]["expect"] == []


def test_the_first_questions_are_a_spread():
    """--limit 20 takes the first turns: they must not all be one kind."""
    first = [q for q in QUESTIONS if "rejected" not in q][:7]
    assert len({q["group"] for q in first}) >= 4
