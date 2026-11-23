"""The signs and pictures: the rules give the right answers, and the server accepts every picture."""

import datetime as dt
import io
import json

import pytest
from PIL import Image

import pictures
import signs
from app.attachments.base import Rejected
from app.attachments.photo import PhotoHandler, sharpness


def at(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%d %H:%M")


@pytest.mark.parametrize("sign,when,minutes,ok", [
    ("two_hours_fee", "2026-11-24 10:00", 180, False), ("two_hours_fee", "2026-11-24 18:30", 180, True),
    ("two_hours_fee", "2026-11-28 10:00", 180, False), ("two_hours_fee", "2026-11-28 16:00", 180, True),
    ("two_hours_fee", "2026-11-29 12:00", 300, True), ("two_hours_fee", "2027-01-05 15:30", 240, True),
    ("two_hours_always_fee", "2026-11-24 22:00", 180, False), ("one_panel", "2026-11-24 22:00", 180, True),
    ("fee_always_two_hours", "2026-11-29 09:00", 180, False), ("fee_always_two_hours", "2026-11-24 22:00", 180, True),
    ("friday", "2026-11-27 10:00", 60, False), ("friday", "2026-06-12 10:00", 60, True),
    ("cleaning", "2026-11-30 13:00", 60, False), ("cleaning", "2026-06-01 13:00", 60, True),
    ("no_stopping", "2026-11-29 23:00", 60, False), ("charging", "2026-11-24 10:00", 60, False),
])
def test_allowed(sign, when, minutes, ok):
    assert signs.allowed(sign, at(when), minutes) is ok


def test_charging_makes_the_charging_bay_allowed():
    assert signs.allowed("charging", at("2026-11-24 10:00"), 60, charging=True) is True
    assert signs.allowed("charging", at("2026-11-24 10:00"), 60, charging=False) is False
    # Charging does not open a no stopping zone.
    assert signs.allowed("no_stopping", at("2026-11-24 10:00"), 60, charging=True) is False


@pytest.mark.parametrize("sign,when,minutes,ok", [
    # The limit counts the time inside its window. 17:00 to 19:30 has 1 hour in 9-18.
    ("two_hours_fee", "2026-11-24 17:00", 150, True),
    # 15:30 to 19:30 has 2.5 hours inside the window.
    ("two_hours_fee", "2026-11-24 15:30", 240, False),
    # Saturday 14:00 to 18:00: the window of a day before a red day is 9-15, so 1 hour counts.
    ("two_hours_fee", "2026-11-28 14:00", 240, True),
])
def test_a_stay_that_crosses_the_end_of_a_window(sign, when, minutes, ok):
    assert signs.allowed(sign, at(when), minutes) is ok


@pytest.mark.parametrize("sign,when,minutes,ok", [
    # A limit with no times runs through midnight.
    ("two_hours_always_fee", "2026-11-24 22:00", 180, False),
    ("two_hours_always_fee", "2026-11-24 22:00", 120, True),
    # Thursday night into Friday: the ban is 8-16 on Fridays only.
    ("friday", "2026-11-26 23:00", 180, True),
    ("friday", "2026-11-27 06:30", 150, False),
    # Friday into Saturday, and Saturday into a red Sunday, where the 2 hour panel is silent.
    ("two_hours_fee", "2026-11-27 17:00", 300, True),
    ("two_hours_fee", "2026-11-28 14:00", 600, True),
    # Sunday night into Monday: the cleaning ban starts at 12 on Monday.
    ("cleaning", "2026-11-29 23:00", 180, True),
    ("cleaning", "2026-11-30 10:00", 180, False),
])
def test_a_stay_that_crosses_midnight(sign, when, minutes, ok):
    assert signs.allowed(sign, at(when), minutes) is ok


@pytest.mark.parametrize("when,needed", [
    ("2026-11-24 18:15", True), ("2026-11-24 19:30", False), ("2026-11-28 10:00", False),
    ("2026-11-28 15:30", True), ("2026-11-29 12:00", False), ("2027-01-05 17:30", False),
    ("2027-01-06 12:00", False),
])
def test_fee_on_two_hours_fee(when, needed):
    assert signs.fee("two_hours_fee", at(when)) is needed


def test_fee_follows_the_panel_that_holds_it():
    assert signs.fee("fee_always_two_hours", at("2026-11-24 03:00")) is True
    assert signs.fee("two_hours_always_fee", at("2026-11-24 10:00")) is True
    assert signs.fee("two_hours_always_fee", at("2026-11-24 22:00")) is False
    assert signs.fee("friday", at("2026-11-27 10:00")) is False


def test_day_types():
    day = dt.date.fromisoformat
    assert signs.day_type(day("2026-12-24")) == "before_red"
    assert signs.day_type(day("2027-01-05")) == "before_red"
    assert signs.day_type(day("2026-12-25")) == "red"
    assert signs.day_type(day("2027-01-06")) == "red"
    assert signs.day_type(day("2026-11-28")) == "before_red"
    assert signs.day_type(day("2026-11-29")) == "red"
    assert signs.day_type(day("2026-11-24")) == "weekday"
    # Easter 2026 and 2027, and a Midsummer Day on a Saturday.
    assert signs.day_type(day("2026-04-03")) == "red"
    assert signs.day_type(day("2026-04-02")) == "before_red"
    assert signs.day_type(day("2027-03-29")) == "red"
    assert signs.day_type(day("2026-06-20")) == "red"


def test_every_sign_of_the_car_is_known():
    ids = [s["id"] for s in json.loads(signs.SIGNS_JSON.read_text(encoding="utf-8"))]
    assert len(ids) == 8
    assert signs.SIGN_IDS == ids


def test_the_rules_are_read_from_the_rows():
    two_hours = signs.rules("two_hours_fee")
    assert [p.limit_minutes for p in two_hours.panels] == [120, None]
    assert [p.fee for p in two_hours.panels] == [False, True]
    assert signs.rules("charging").panels[0].charging_only
    assert signs.rules("cleaning").panels[1].forbids
    assert signs.rules("friday").top == "no_parking"


@pytest.mark.parametrize("sign", signs.SIGN_IDS)
def test_a_drawn_sign_is_accepted_by_the_photo_handler(sign):
    data = signs.draw(sign)
    assert data.startswith(b"\xff\xd8\xff")
    parts = PhotoHandler().process(data, "sign")
    assert parts[0].content["type"] == "input_image"
    assert sharpness(Image.open(io.BytesIO(data))) > 120  # well over the server's limit of 60


@pytest.mark.parametrize("sign", signs.SIGN_IDS)
def test_the_smallest_row_is_at_least_24_pixels(sign):
    assert signs.layout(sign).row_px >= 24


def test_the_signs_differ():
    assert len({signs.draw(s) for s in signs.SIGN_IDS}) == 8


@pytest.mark.parametrize("make", [
    lambda: pictures.broken_lamp("left"), lambda: pictures.broken_lamp("right"), pictures.undamaged,
])
def test_a_picture_of_the_car_is_accepted_by_the_photo_handler(make):
    assert PhotoHandler().process(make(), "car")


def test_the_two_broken_lamps_differ_and_the_crack_is_drawn():
    left, right, fine = pictures.broken_lamp("left"), pictures.broken_lamp("right"), pictures.undamaged()
    assert len({left, right, fine}) == 3


def test_a_blurred_sign_is_rejected_as_too_blurry():
    with pytest.raises(Rejected) as caught:
        PhotoHandler().process(pictures.blurred("two_hours_fee"), "sign")
    assert caught.value.code == "too_blurry"


def test_a_broken_lamp_needs_a_side():
    with pytest.raises(ValueError):
        pictures.broken_lamp("middle")


def test_the_font_has_the_swedish_letters():
    font = signs._font(32)
    assert font.getmask("å").getbbox() != font.getmask("￿").getbbox()
