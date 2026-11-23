"""Reading an answer, and the context a car sends.

Shared by the routing check, the car tool check and the tests.
"""

from __future__ import annotations

import datetime as dt
import re

YES = {"yes", "yep", "yeah", "sure", "absolutely", "certainly", "ja"}
NO = {"no", "nope", "nej", "not"}
# A clear way to say it without starting with the word, judged on the first sentence only.
NO_PATTERNS = (
    r"\byou (?:can ?not|can't|cannot|may not|must not|are not allowed|aren't allowed|should not|shouldn't)\b",
    r"\bnot allowed\b", r"\bnot permitted\b", r"\bis (?:prohibited|forbidden)\b",
)
YES_PATTERNS = (
    r"\byou (?:can|may|are allowed|'re allowed)\b", r"\bis (?:allowed|permitted)\b",
)


def read_verdict(text: str) -> str | None:
    """"yes" or "no" when the answer starts with one, or says it clearly in its first sentence."""
    lowered = re.sub(r"^[\W_]+", "", text.strip().lower())
    if re.match(r"no (?:problem|worries)\b", lowered):
        return "yes"
    first = re.match(r"[a-zåäö]+", lowered)
    if first and first.group(0) in YES:
        return "yes"
    if first and first.group(0) in NO:
        return "no"
    sentence = re.split(r"[.!?\n]", lowered, maxsplit=1)[0]
    if any(re.search(p, sentence) for p in NO_PATTERNS):
        return "no"
    if any(re.search(p, sentence) for p in YES_PATTERNS):
        return "yes"
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
