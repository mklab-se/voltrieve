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
        ("Storgatan 1, 753 20 Uppsala", "[address] Uppsala"),
        ("to Västra Hamngatan 12B", "to [address]"),
        ("Ringvägen 100 lgh 1102", "[address]"),
        ("in 753 20 Uppsala", "in [postal code] Uppsala"),
    ],
)
def test_mask_replaces(text, expected):
    assert mask(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Atlas AWD 2026", "VT-1042-11", "VP-10007", "Fjord 2", "529 000 SEK", "wants to charge at home", "part 4711-A", "ABCDEFGHJKLMNPRST", "ABCD 1234"],
)
def test_mask_keeps_readable(text):
    assert mask(text) == text


def test_mask_takes_ordinary_words_that_look_like_plates():
    assert mask("AWD 400") == "[plate]"  # over-masking is the safe side
    assert mask("kw 150") == "kw 150"  # two letters are not a plate


SEPARATORS = ["", " ", "  ", " - ", "-", "\u00a0", "\u2011", "\u2013", "\u2014", "\u2212", "\u00ad", "\u200b", "\u2060 \u200b"]


@pytest.mark.parametrize("sep", SEPARATORS)
def test_plate_separators(sep):
    text = f"bil ABC{sep}123 här"
    assert mask(text) == "bil [plate] här"
    assert allowed(text, ()) is False
    assert allowed(f"bil abc{sep}12d", ()) is False


@pytest.mark.parametrize("text", ["_ABC123_", "ABC_123", "reg_ABC123", "ABC_12D"])
def test_underscore_does_not_hide_a_plate(text):
    assert "[plate]" in mask(text)


def test_vin_before_phone_and_email():
    assert mask("070-123 45 67 1HGCM82633A004352") == "[phone] [vin]"
    assert mask("Ring 0701234567\n1HGCM82633A004352") == "Ring [phone]\n[vin]"
    assert mask("1HGCM82633A004352@example.se") == "[email]"
    assert mask("a.1HGCM82633A004352@example.se") == "[email]"


def test_chained_emails_are_masked_whole():
    assert mask("anna@example.se+bo@example.se") == "[email]"


def test_a_text_with_every_kind():
    text = "Anna anna+x@example.se, 070-123 45 67, 19800101-1234, ABC 123 and 1HGCM82633A004352 want VT-1042-11"
    assert mask(text) == "Anna [email], [phone], [id number], [plate] and [vin] want VT-1042-11"


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
        "live at Torget 5",
        "in 753 20 Uppsala",
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


@pytest.mark.parametrize(
    ("text", "registration"),
    [
        ("A B", "AB"),
        ("A-B", "AB"),
        ("a 1", "A1"),
        ("e.v.", "EV"),
        ("I\u2665U", "IU"),
        ("my a_b car", "ab"),
    ],
)
def test_a_short_registration_written_loosely(text, registration):
    assert allowed(text, (registration,)) is False


def test_a_short_registration_inside_a_word_is_fine():
    assert allowed("a big car", ("AB",)) is True
    assert allowed("every road", ("EV",)) is True


@pytest.mark.parametrize("text", ["code 20\u200b26", "anna\uff20example.se", "code 2\u00ad0 26", "WVWZZZ\u200b1JZXW000001"])
def test_allowed_sees_through_format_characters_and_fullwidth(text):
    assert allowed(text, ()) is False


def test_a_registration_of_only_punctuation_is_ignored():
    assert allowed("wants a wall-box", ("-",)) is True
    assert allowed("wants a wall-box", (" . ", "")) is True


def test_allowed_handles_several_registrations():
    assert allowed("loves the opel", ("SPARK", "OPEL")) is False
    assert allowed("loves the volvo", ("SPARK", "OPEL")) is True


@pytest.mark.parametrize("text", ["wants \ud800 help", "\udfff", "a\ud83d"])
def test_a_text_that_cannot_be_encoded_is_not_allowed(text):
    assert allowed(text, ()) is False
