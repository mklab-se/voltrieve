"""Reading a verdict out of an answer, and the context of the car."""

import pytest

import answers
from app.context import ClientContext, context_line


@pytest.mark.parametrize("text,verdict", [
    ("Yes, you can park here for 3 hours.", "yes"),
    ("yes - until 18.", "yes"),
    ("**Yes.** The limit does not apply in the evening.", "yes"),
    ("No, the limit is 2 hours between 9 and 18.", "no"),
    ("\"No\": it is a no stopping zone.", "no"),
    ("Nej, inte nu.", "no"),
    ("No problem, you may park here.", "yes"),
    ("You can park here, the fee does not apply on Sundays.", "yes"),
    ("You cannot park here on Mondays between 12 and 15.", "no"),
    ("Parking is not allowed here at that time.", "no"),
    ("Not on a Friday between 8 and 16.", None),
    ("Not before 19:00.", None),
    ("Yes, but only for two hours.", None),
    ("Yes, but not for 3 hours.", None),
    ("Yes, except on Sundays.", None),
    ("Yes, unless the street is cleaned.", None),
    ("Not allowed here.", "no"),
    ("You may park for up to two hours.", "yes"),
    ("The sign says two hours, so it depends on the time.", None),
    ("I cannot see a parking sign in your photo.", None),
    ("", None),
])
def test_read_verdict(text, verdict):
    assert answers.read_verdict(text) == verdict


def test_only_the_first_sentence_counts():
    assert answers.read_verdict("You can park here. No fee is needed on Sundays.") == "yes"
    assert answers.read_verdict("No. You can park elsewhere.") == "no"


@pytest.mark.parametrize("when,offset", [
    ("2026-11-24 10:00", "+01:00"), ("2026-06-01 13:00", "+02:00"), ("2026-12-24 12:00", "+01:00"),
    ("2027-01-05 15:30", "+01:00"), ("2026-03-29 12:00", "+02:00"), ("2026-10-25 12:00", "+01:00"),
])
def test_the_context_follows_swedish_summer_time(when, offset):
    context = answers.car_context(when)
    assert context["local_time"].endswith(offset)
    assert context["local_time"].startswith(when.replace(" ", "T"))


def test_the_context_is_accepted_by_the_api_and_says_the_time():
    context = answers.car_context("2026-11-24 10:00", speed_kmh=30)
    line = context_line(ClientContext.model_validate(context))
    assert "Tue 24 Nov 2026 10:00" in line and "Voltrieve Spark" in line and "speed 30 km/h" in line
