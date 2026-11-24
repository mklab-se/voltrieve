"""Private turns: what they say, and error texts that may quote it, stay out of the logs. Failures stay
diagnosable through the error type, the HTTP status and the stack as frames."""

import logging

import pytest
from voltrieve_search.client import SearchApiError

from voltrieve_agent.knowledge import KnowledgeBase
from voltrieve_agent.loop import TurnInput, run_turn
from voltrieve_agent.model import Completed, Failed, FunctionCall, TextDelta
from voltrieve_agent.private import current_private, frames_only, log_exception
from voltrieve_agent.server_tools import CURATED_TOOL, build_server_tools
from voltrieve_agent.tools import ServerTool

ANY_OBJECT = {"type": "object", "properties": {}}
PRIVATE = {
    "context": {"private": True},
    "parts": {"parts": [{"type": "input_text", "text": "SPARK"}]},
    "kept": {"kept": {"items": [], "head": [], "text": ""}},
}


class ApiError(Exception):
    status_code = 429


class Model:
    def __init__(self, *rounds, record_error=None):
        self.rounds = list(rounds)
        self.record_error = record_error

    async def stream(self, **kwargs):
        for item in self.rounds.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item

    async def record(self, conversation, items):
        if self.record_error:
            raise self.record_error


def fail(secret="SPARK"):
    plate = secret
    try:
        raise ValueError(f"first {plate}")
    except ValueError as exc:
        raise ApiError(f"second {plate}") from exc


def text_of(call) -> str:
    try:
        call()
    except Exception as exc:
        return frames_only(exc)


def test_an_error_is_shown_by_type_status_and_frames():
    text = text_of(fail)
    lines = text.splitlines()
    assert lines[0] == "ApiError, HTTP status 429"
    assert "ValueError" in text and "test_private.py" in text and " in fail" in text
    assert "SPARK" not in text and "second" not in text and "raise" not in text and "plate" not in text


def test_chained_grouped_and_circular_errors_show_no_text():
    def context_only():
        try:
            raise ValueError("SPARK")
        except ValueError:
            raise KeyError("SPARK")

    def group():
        raise ExceptionGroup("SPARK", [ValueError("SPARK"), ExceptionGroup("SPARK", [TypeError("SPARK")])])

    def circular():
        a, b = ValueError("SPARK"), KeyError("SPARK")
        a.__cause__, b.__cause__ = b, a
        raise a

    def noted():
        error = ValueError("SPARK")
        error.add_note("SPARK")
        raise error

    for call, types in (
        (context_only, ("KeyError", "ValueError")),
        (group, ("ExceptionGroup", "ValueError", "TypeError")),
        (circular, ("ValueError", "KeyError")),
        (noted, ("ValueError",)),
    ):
        text = text_of(call)
        assert "SPARK" not in text and all(t in text for t in types)


def test_a_syntax_error_and_a_long_stack_stay_bounded_and_clean():
    def deep():
        return deep()

    assert len(text_of(deep)) < 20_000
    assert "SPARK" not in text_of(lambda: compile("SPARK = = 1", "SPARK.py", "exec"))


def test_a_status_that_is_not_a_number_is_not_shown():
    class Odd(Exception):
        status_code = "SPARK"

    def odd():
        raise Odd

    assert text_of(odd).splitlines()[0] == "Odd"


class Hostile(Exception):
    """Both its statuses fail when they are read."""

    @property
    def status_code(self):
        raise RuntimeError("SPARK status_code")

    @property
    def status(self):
        raise ValueError("SPARK status")


class HostileInt(int):
    def __format__(self, spec):
        raise RuntimeError("SPARK format")


class HostileGroup(ExceptionGroup):
    @property
    def exceptions(self):
        raise RuntimeError("SPARK exceptions")


def test_one_broken_status_does_not_hide_the_other_or_the_frames():
    class Half(Exception):
        status = 503

        @property
        def status_code(self):
            raise RuntimeError("SPARK")

    def half():
        raise Half

    assert text_of(half).startswith("Half, HTTP status 503\n") and " in half" in text_of(half)


def hostile():
    raise Hostile


def hostile_number():
    error = ApiError()
    error.status_code = HostileInt(400)
    raise error


def hostile_group():
    raise HostileGroup("SPARK", [ValueError("SPARK")])


@pytest.mark.parametrize("call", [hostile, hostile_number, hostile_group])
def test_logging_a_private_failure_never_raises(caplog, call):
    try:
        call()
    except Exception as exc:
        name = type(exc).__name__
        log_exception(logging.getLogger("x"), "It failed", private=True)
    assert f"It failed: {name}" in caplog.text
    assert "SPARK" not in caplog.text and not caplog.records[0].exc_info


def test_logging_outside_an_error_does_not_raise(caplog):
    log_exception(logging.getLogger("x"), "It failed", private=True)
    assert "It failed" in caplog.text


async def test_a_hostile_error_does_not_end_a_private_turn(user, events):
    turn = TurnInput(user, "c", "t", "hi", private=True)
    out = await run_turn(tool_rounds(), [failing_tool(Hostile())], turn, events)
    assert out.status == "done"


def test_log_exception_is_the_ordinary_one_outside_a_private_turn(caplog):
    for private in (False, True, None):
        try:
            fail()
        except ApiError:
            log_exception(logging.getLogger("x"), "It %s", "failed", private=private)
    ordinary, hidden, default = caplog.records
    assert ordinary.getMessage() == "It failed" and ordinary.exc_info
    assert hidden.getMessage().startswith("It failed: ApiError, HTTP status 429\n") and not hidden.exc_info
    assert default.exc_info
    assert all(r.levelno == logging.ERROR and r.funcName.startswith("test_log") for r in caplog.records)


def failing_tool(error):
    async def handler(args, user):
        raise error

    return ServerTool("check_stock", "d", ANY_OBJECT, handler)


def tool_rounds():
    call = FunctionCall("c1", "check_stock", "{}")
    return Model([call, Completed({})], [TextDelta("Sorry"), Completed({})])


async def fails_in_every_way(user, events, monkeypatch, **turn):
    """Every place in the loop that logs a failure. Returns nothing: the log is what matters."""

    def refuse(*args, **kwargs):
        raise SearchApiError(402, "no allowance for SPARK")

    monkeypatch.setattr("voltrieve_agent.knowledge.retrieve", refuse)
    knowledge = KnowledgeBase("https://search", None)

    async def search(args, user):
        return await knowledge.search("SPARK")

    def new():
        return TurnInput(user, "c", "t", [] if "kept" in turn else "SPARK", **turn)

    search_tool = ServerTool("check_stock", "d", ANY_OBJECT, search)
    await run_turn(tool_rounds(), [failing_tool(ApiError("SPARK"))], new(), events)
    await run_turn(tool_rounds(), [search_tool], new(), events)
    await run_turn(Model([ApiError("SPARK")]), [], new(), events)
    await run_turn(Model([Failed("model_failed", "SPARK")]), [], new(), events)
    curated = [FunctionCall("c1", CURATED_TOOL, '{"topic": "contact"}'), Completed({})]
    broken = Model(curated, record_error=ApiError("SPARK"))
    await run_turn(broken, build_server_tools(None, None), new(), events)
    call = FunctionCall("c1", "check_stock", "{}")
    closing = Model([call, Completed({})], [ApiError("SPARK")])
    await run_turn(closing, [failing_tool(ApiError("SPARK"))], new(), events, max_iterations=1)


@pytest.mark.parametrize("kind", PRIVATE)
async def test_no_failure_of_a_private_turn_is_logged_with_its_text(user, events, caplog, monkeypatch, kind):
    with caplog.at_level(logging.DEBUG):
        await fails_in_every_way(user, events, monkeypatch, **PRIVATE[kind])
    assert "SPARK" not in caplog.text and not [r for r in caplog.records if r.exc_info]
    assert "SPARK" not in str([r.__dict__ for r in caplog.records])
    for expected in ("Tool check_stock failed: ApiError, HTTP status 429", "in handler", "Search refused",
                     "HTTP status 402", "Model stream failed: ApiError", "Model reported model_failed"):
        assert expected in caplog.text
    assert current_private.get() is False


async def test_every_failure_of_an_ordinary_turn_is_logged_with_its_text(user, events, caplog, monkeypatch):
    with caplog.at_level(logging.DEBUG):
        # A private turn before it must not leave the log quiet.
        await run_turn(Model([TextDelta("x")]), [], TurnInput(user, "c", "t", "hi", private=True), events)
        await fails_in_every_way(user, events, monkeypatch)
    for expected in ("Tool check_stock failed", "Search refused voltrieve-kb: no allowance for SPARK",
                     "Model stream failed", "Model reported model_failed: SPARK",
                     "Could not record the final answer", "Closing model call failed"):
        assert expected in caplog.text
    assert len([r for r in caplog.records if r.exc_info]) == 5


async def test_a_host_can_make_everything_around_the_turn_private(user, events, caplog):
    token = current_private.set(True)
    try:
        await run_turn(Model([ApiError("SPARK")]), [], TurnInput(user, "c", "t", "hi"), events)
    finally:
        current_private.reset(token)
    assert "SPARK" not in caplog.text


def test_a_turn_with_an_attachment_is_private_whatever_the_host_says(user):
    assert not TurnInput(user, "c", "t", "hi").private
    for extra in PRIVATE.values():
        assert TurnInput(user, "c", "t", "hi", **{"private": False, **extra}).private


def test_mask_contacts_replaces_email_phone_and_id_number():
    from voltrieve_agent.private import mask_contacts

    text = "mail anna+ev@example.se, call +46 70 123 45 67 or 070-123 45 67, id 19800101-1234 and 8001011234"
    assert mask_contacts(text) == "mail [email], call [phone] or [phone], id [id number] and [id number]"
    assert mask_contacts("Atlas AWD 2026 costs 1 500 kr, VT-1042-11") == "Atlas AWD 2026 costs 1 500 kr, VT-1042-11"


def test_mask_contacts_keeps_whitespace_and_length():
    from voltrieve_agent.private import mask_contacts

    assert mask_contacts("a  b\n" + "x" * 300) == "a  b\n" + "x" * 300


def test_mask_contacts_is_fast_on_long_text():
    import time

    from voltrieve_agent.private import mask_contacts

    for chunk in ("a", "a.", "a@", "a@b", "0 ", "0" * 5 + " ", "+4 ", "1"):
        started = time.perf_counter()
        mask_contacts(chunk * (100_000 // len(chunk)))
        assert time.perf_counter() - started < 1, chunk


def test_mask_contacts_takes_chained_emails_whole():
    from voltrieve_agent.private import mask_contacts

    assert mask_contacts("anna@example.se+bo@example.se") == "[email]"
    assert mask_contacts("x anna@example.se+bo@example.se. y") == "x [email] y"


def test_new_email_pattern_covers_everything_the_old_one_did():
    import random
    import re

    from voltrieve_agent.private import _EMAIL

    old = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")  # the pattern before the lookbehind

    def covered(pattern, text):
        marks = set()
        for m in pattern.finditer(text):
            marks.update(range(m.start(), m.end()))
        return marks

    rng = random.Random(7)
    alphabet = "ab1_.+-@ \n,"
    for _ in range(20_000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
        assert covered(old, text) <= covered(_EMAIL, text), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Storgatan 1, 753 20 Uppsala", "[address] Uppsala"),
        ("Västra Hamngatan 12B", "[address]"),
        ("Kungsvägen 3", "[address]"),
        ("Ringvägen 100 lgh 1102", "[address]"),
        ("Torget 5", "[address]"),
        ("send it to storgatan 1 please", "send it to [address] please"),
        ("Olof Palmes gata 7, lägenhet 12", "[address]"),
        ("Allégränd 4\nSE-753 20 Uppsala", "[address] Uppsala"),
        ("Björkallén 12-14, 75320 Uppsala", "[address] Uppsala"),
        ("KUNGSVÄGEN 3", "[address]"),
        ("Kungsva\u0308gen 3", "[address]"),
        ("Kungsvagen 3", "[address]"),
        ("Storgatan nr 5", "[address]"),
        ("Vägen 5", "[address]"),
        ("Evas väg 5", "[address]"),
        ("torget 5", "[address]"),
        ("Storgatan 12M", "[address]"),
        ("Storgatan 12m", "[address]"),
        ("Storgatan 12 m", "[address] m"),
        ("421 30 V Frölunda", "[postal code] V Frölunda"),
        ("753 20 uppsala", "[postal code] uppsala"),
        ("753 20", "[postal code]"),
        ("753 20 Vänersborg", "[postal code] Vänersborg"),
        ("753 20 Mora", "[postal code] Mora"),
        ("I live in 753 20 Uppsala", "I live in [postal code] Uppsala"),
        ("SE-753 20, S-211 22", "[postal code], [postal code]"),
        ("753\u00a020 Uppsala", "[postal code] Uppsala"),
        ("Storgatan 1, 070-123 45 67", "[address], [phone]"),
    ],
)
def test_mask_contacts_replaces_addresses(text, expected):
    from voltrieve_agent.private import mask_contacts

    assert mask_contacts(text) == expected


@pytest.mark.parametrize(
    "text",
    ["529 000 SEK", "VP-10007", "Fjord 2", "100 km", "model year 2026", "1 500 kr", "VP-753 20", "Order 2 Atlas",
     "costs 1 529 00", "a service plan 3", "page 12 of the guide", "brake discs 345 mm", "529000 SEK",
     "gatan", "the 250 kW charger", "245 45 R20", "400 20-inch", "345 40 Nm", "250 50 percent", "400 12 V",
     "250 50 %", "400 20 inch wheels", "körväg 300 km", "en väg 35 mil", "på väg 2026", "På väg 2026",
     "körväg 12-14 km", "vägen 300 m", "Ringvägen 100 km", "leasingväg 500 kr", "väg 900 SEK", "vägen 5"],
)
def test_mask_contacts_keeps_what_is_not_an_address(text):
    from voltrieve_agent.private import mask_contacts

    assert mask_contacts(text) == text


def test_mask_contacts_is_fast_on_long_text_that_looks_like_addresses():
    import time

    from voltrieve_agent.private import mask_contacts

    for chunk in ("A", "Ab ", "A ", "gata", "gatan ", "Gatan ", "Gatan 1", "Gatan 1 ", "väg", "Väg  ", "vägen nr ",
                  "753 ", "753 20 ", "753 20 k", "753 20 R", "väg 3 k", "Abc väg 1-", "Abc ", "SE-", "S-753 ", "ä", "va\u0308g", " ", "Torget 5, ", "lgh "):
        for text in (chunk * (100_000 // len(chunk)), "Gatan" + " " * 50_000 + chunk * (50_000 // len(chunk))):
            started = time.perf_counter()
            mask_contacts(text)
            assert time.perf_counter() - started < 1, chunk
