import time

import pytest

from voltrieve_insights.masking import allowed, mask


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my plate is ABC 123 ok", "my plate is [plate] ok"),
        ("abc12d", "[plate]"),
        ("ABC123", "[plate]"),
        ("ABC 12D", "[plate]"),
        ("ABC-123", "[plate]"),
        ("ABC 123", "[plate]"),
        ("Bilen abc 12d står på gatan", "Bilen [plate] står på gatan"),
        ("WVWZZZ1JZXW000001", "[vin]"),
        ("vin wvwzzz1jzxw000001.", "vin [vin]."),
        ("write to anna@example.se", "write to [email]"),
        ("anna+ev@example.se", "[email]"),
        ("ring +46 70 123 45 67", "ring [phone]"),
        ("ring 070-123 45 67", "ring [phone]"),
        ("id 19800101-1234", "id [id number]"),
        ("id 198001011234", "id [id number]"),
        ("id 800101-1234", "id [id number]"),
        ("id 8001011234", "id [id number]"),
    ],
)
def test_mask_replaces(text, expected):
    assert mask(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Atlas AWD 2026", "VT-1042-11", "wants to charge at home", "part 4711-A", "ABCDEFGHJKLMNPRST", "ABCD 1234"],
)
def test_mask_keeps_readable(text):
    assert mask(text) == text


@pytest.mark.parametrize("text", ["AWD 400", "kw 150"])
def test_mask_may_take_ordinary_words_that_look_like_plates(text):
    assert mask(text) in (text, "[plate]")


def test_vin_needs_a_digit_and_no_i_o_q():
    assert mask("ABCDEFGHJKLMNPRSTU") == "ABCDEFGHJKLMNPRSTU"
    assert mask("WVWZZZ1JZXW00000I") == "WVWZZZ1JZXW00000I"


def test_mask_is_fast_on_long_text():
    for chunk in ("a", "a.", "a@", "0 ", "ABC ", "ABC 12 ", "WVWZZZ1JZXW00000A", "1", "A1"):
        text = chunk * (100_000 // len(chunk))
        started = time.perf_counter()
        mask(text)
        assert time.perf_counter() - started < 1, chunk


@pytest.mark.parametrize(
    "text",
    [
        "my plate is ABC 123",
        "abc12d",
        "ABC-123",
        "ABC 123",
        "WVWZZZ1JZXW000001",
        "anna@example.se",
        "anna+ev@example.se",
        "call +46 70 123 45 67",
        "call 070-123 45 67",
        "id 19800101-1234",
        "id 8001011234",
        "the year 2026",
        "code 2 0 2 6",
        "code 2 0 2\n6",
        "code ２０２６",
        "code ٢٠٢٦",
        "x" * 101,
    ],
)
def test_allowed_refuses(text):
    assert allowed(text, ()) is False


@pytest.mark.parametrize("text", ["wants to charge at home without a wall box", "has 3 kids and 2 dogs", "x" * 100])
def test_allowed_accepts(text):
    assert allowed(text, ()) is True
    assert allowed(text, ("ZZZ999",)) is True


@pytest.mark.parametrize(
    "text",
    ["spark", "SPARK", "Spark", "my SpArK is red", "sp ark", "s-park", "the car spark again", "ＳＰＡＲＫ"],
)
def test_allowed_refuses_a_registration_in_any_case_or_split(text):
    assert allowed(text, ("SPARK",)) is False
    assert allowed(text, ("other", "spark")) is False


def test_allowed_compares_a_registration_with_spaces_and_hyphens_loosely():
    assert allowed("find ab-c 1 2 d", ("ABC 12D",)) is False
    assert allowed("find abc12d", ("ABC-12d",)) is False


def test_a_short_registration_is_a_whole_word():
    assert allowed("wants fast charger", ("A",)) is True
    assert allowed("wants a fast charger", ("A",)) is False
    assert allowed("wants a charger", ("a",)) is False
    assert allowed("the ev is nice", ("EV",)) is False
    assert allowed("every road", ("EV",)) is True
    assert allowed("wants to charge", ("",)) is True


def test_allowed_handles_several_registrations():
    assert allowed("loves the opel", ("SPARK", "OPEL")) is False
    assert allowed("loves the volvo", ("SPARK", "OPEL")) is True
