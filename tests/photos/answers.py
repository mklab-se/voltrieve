"""Reading an answer, and the context a car sends.

Shared by the routing check, the car tool check and the tests.
"""

from __future__ import annotations

import datetime as dt
import re

YES = {"yes", "yep", "yeah", "sure", "absolutely", "certainly", "ja"}
NO = {"no", "nope", "nej"}
# A clear way to say it without starting with the word, judged on the first sentence only.
NO_PATTERNS = (
    r"\byou (?:can ?not|can't|cannot|may not|must not|are not allowed|aren't allowed|should not|shouldn't)\b",
    r"\bnot allowed\b", r"\bnot permitted\b", r"\bis (?:prohibited|forbidden)\b",
)
YES_PATTERNS = (
    r"\byou (?:can|may|are allowed|'re allowed)\b", r"\bis (?:allowed|permitted)\b",
)


# A yes with one of these in its first sentence is a condition, not a yes: "Yes, but only for 2 hours".
HEDGES = re.compile(r"\b(?:but|except|unless|only|if|depends|provided|however|as long as)\b")
AFTER_YES = re.compile(r"\b(?:but|except|unless|only|if|depends|provided|however|as long as|not)\b")  # after a yes, "not" is a condition too
# "Not allowed" is a no. Any other "Not ..." ("Not before 19:00", "Not for 3 hours") is a condition.
PLAIN_NOT = re.compile(r"not (?:allowed|permitted|here|now|possible)\b")


def read_verdict(text: str) -> str | None:
    """"yes" or "no" when the answer starts with one, or says it clearly in its first sentence.
    A hedged answer ("Yes, but only...", "Not before 19:00") is None: unclear, for a person to judge."""
    lowered = re.sub(r"^[\W_]+", "", text.strip().lower())
    sentence = re.split(r"[.!?\n]", lowered, maxsplit=1)[0]
    if re.match(r"no (?:problem|worries)\b", lowered):
        return "yes"
    first = re.match(r"[a-zåäö]+", lowered)
    word = first.group(0) if first else ""
    if word in YES:
        return None if AFTER_YES.search(sentence) else "yes"
    if word in NO:
        return "no"
    if word == "not":
        return "no" if PLAIN_NOT.match(lowered) else None
    if any(re.search(p, sentence) for p in NO_PATTERNS):
        return "no"
    if any(re.search(p, sentence) for p in YES_PATTERNS):
        return None if HEDGES.search(sentence) else "yes"
    return None


def _last_sunday(year: int, month: int) -> dt.date:
    day = dt.date(year, month, 31)
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def swedish_offset(moment: dt.datetime) -> str:
    """+02:00 in summer time (last Sunday of March to last Sunday of October), else +01:00."""
    start = dt.datetime.combine(_last_sunday(moment.year, 3), dt.time(2))
    end = dt.datetime.combine(_last_sunday(moment.year, 10), dt.time(3))
    return "+02:00" if start <= moment < end else "+01:00"


def car_context(when: str, speed_kmh: int = 0) -> dict:
    """What the car sends with a message, as the car client does (clients/car/src/context.ts).
    `when` is local time, like "2026-11-24 10:00"."""
    moment = dt.datetime.strptime(when, "%Y-%m-%d %H:%M")
    return {
        "car": {"model": "Voltrieve Spark", "trim": "Prime", "drive": "RWD", "model_year": 2026,
                "registration": "SPARK", "odometer_km": 8214},
        "local_time": moment.strftime("%Y-%m-%dT%H:%M:%S") + swedish_offset(moment),
        "speed_kmh": speed_kmh,
    }
