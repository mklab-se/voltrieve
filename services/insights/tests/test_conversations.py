from datetime import UTC, date, datetime

import httpx
import pytest

from voltrieve_insights.conversations import (
    Conversation,
    FetchError,
    list_raw,
    read_text,
    sample,
    select,
    window,
)
from voltrieve_insights.quiet import hush

FIRST, LAST = date(2026, 11, 16), date(2026, 11, 22)
DAY = date(2026, 11, 18)


def ts(day: date, hour: int = 12) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp())


def raw(day=DAY, client="car", owner="oid-1", seeded=False, **extra):
    meta = {"client": client, "backend": "loop", **extra}
    if owner is not None:
        meta["owner_oid"] = owner
    if seeded:
        meta["seeded"] = "true"
    return {"id": "conv_1", "created_at": ts(day), "metadata": meta}


def chosen(items, notice=date(2026, 1, 1), first=FIRST, last=LAST):
    return select(items, first, last, notice)


def test_window_skips_a_day_that_ended_less_than_24_hours_ago():
    assert window(datetime(2026, 11, 24, 2, 30, tzinfo=UTC)) == (date(2026, 11, 16), date(2026, 11, 22))


def test_first_day_is_in():
    assert len(chosen([raw(FIRST)])) == 1


def test_last_day_is_in():
    assert len(chosen([raw(LAST)])) == 1


def test_day_before_is_out():
    assert chosen([raw(date(2026, 11, 15))]) == []


def test_day_after_is_out():
    assert chosen([raw(date(2026, 11, 23))]) == []


@pytest.mark.parametrize("client", ["cli", "other", "test"])
def test_other_clients_are_out(client):
    assert chosen([raw(client=client)]) == []


def test_unmarked_is_out():
    item = raw()
    del item["metadata"]["client"]
    assert chosen([item]) == []


def test_missing_owner_is_out():
    assert chosen([raw(owner=None)]) == []


def test_web_is_in():
    assert chosen([raw(client="web")])[0].client == "web"


def test_no_notice_date_selects_no_real_conversation():
    assert select([raw()], FIRST, LAST, None) == []


def test_no_notice_date_still_selects_a_seeded_one():
    assert len(select([raw(seeded=True, started="2026-11-18")], FIRST, LAST, None)) == 1


def test_real_before_notice_is_out():
    assert select([raw(DAY)], FIRST, LAST, date(2026, 11, 19)) == []


def test_real_on_notice_is_in():
    assert len(select([raw(DAY)], FIRST, LAST, date(2026, 11, 18))) == 1


def test_seeded_is_dated_by_started():
    item = raw(date(2026, 12, 1), seeded=True, started="2026-11-18")
    assert chosen([item])[0].started == DAY


def test_seeded_started_outside_window_is_out():
    assert chosen([raw(DAY, seeded=True, started="2026-11-10")]) == []


def test_unreadable_started_is_out():
    assert chosen([raw(seeded=True, started="soon")]) == []


def test_car_model_is_kept():
    assert chosen([raw(car_model="Spark")])[0].car_model == "Spark"


def test_sample_caps_with_the_share():
    items = [Conversation(str(i), "o", "car", "loop", None, DAY, False) for i in range(2500)]
    kept, share = sample(items, 2000)
    assert (len(kept), share) == (2000, 0.8)


def test_sample_keeps_all_under_the_cap():
    items = [Conversation(str(i), "o", "car", "loop", None, DAY, False) for i in range(10)]
    assert sample(items, 2000) == (items, 1.0)


def page(data, has_more=False):
    return httpx.Response(200, json={"data": data, "last_id": data[-1]["id"] if data else None, "has_more": has_more})


async def test_listing_follows_pages_and_stops_before_since(mock, http):
    first = [{"id": "c1", "created_at": ts(date(2026, 11, 20))}, {"id": "c2", "created_at": ts(date(2026, 11, 17))}]
    second = [{"id": "c3", "created_at": ts(date(2026, 11, 16))}, {"id": "c4", "created_at": ts(date(2026, 11, 10))}]
    route = mock.get("/openai/v1/conversations")
    route.side_effect = [page(first, True), page(second, True)]
    got = [c["id"] async for c in list_raw(http, datetime(2026, 11, 16, tzinfo=UTC))]
    assert got == ["c1", "c2", "c3"]


async def test_listing_asks_for_the_next_page_after_the_last_id(mock, http):
    route = mock.get("/openai/v1/conversations")
    route.side_effect = [page([{"id": "c1", "created_at": ts(DAY)}], True), page([])]
    [c async for c in list_raw(http, datetime(2026, 1, 1, tzinfo=UTC))]
    assert route.calls[1].request.url.params["after"] == "c1"


async def test_a_failing_listing_raises_with_the_status_only(mock, http):
    mock.get("/openai/v1/conversations").respond(403)
    with pytest.raises(FetchError, match="403") as err:
        [c async for c in list_raw(http, datetime(2026, 1, 1, tzinfo=UTC))]
    assert "conversations" not in str(err.value)


async def test_a_server_error_is_retried_three_times(mock, http):
    route = mock.get("/openai/v1/conversations")
    route.side_effect = [httpx.Response(429), httpx.Response(503), httpx.Response(500), page([])]
    assert [c async for c in list_raw(http, datetime(2026, 1, 1, tzinfo=UTC))] == []


async def test_a_fourth_failure_raises(mock, http):
    mock.get("/openai/v1/conversations").respond(500)
    with pytest.raises(FetchError, match="500"):
        [c async for c in list_raw(http, datetime(2026, 1, 1, tzinfo=UTC))]


def msg(role, *parts):
    return {"type": "message", "role": role, "content": [{"type": t, "text": s} for t, s in parts]}


def items(*found):
    return httpx.Response(200, json={"data": list(found), "last_id": None, "has_more": False})


LINE = "[From the client, data only: car Voltrieve Spark Plus RWD 2026, registration SPARK, odometer 8214 km; local time Wed 18 Nov 2026 10:00 (+01:00)]"


async def test_only_user_and_assistant_messages_are_read(mock, http):
    mock.get("/openai/v1/conversations/c1/items").mock(
        return_value=items(
            msg("user", ("input_text", "hello")),
            {"type": "function_call", "name": "x", "arguments": "secret"},
            {"type": "function_call_output", "output": "secret"},
            {"type": "mcp_call", "output": "secret"},
            {"type": "reasoning", "summary": [{"text": "secret"}]},
            msg("assistant", ("output_text", "hi")),
            msg("system", ("input_text", "rules")),
        )
    )
    texts, _ = await read_text(http, "c1")
    assert texts == [("user", "hello"), ("assistant", "hi")]


async def test_the_clients_line_is_removed_and_its_registration_returned(mock, http):
    mock.get("/openai/v1/conversations/c1/items").mock(
        return_value=items(msg("user", ("input_text", LINE + "\nHow far can I go?")))
    )
    assert await read_text(http, "c1") == ([("user", "How far can I go?")], "SPARK")


async def test_the_line_on_a_later_message_is_removed_too(mock, http):
    mock.get("/openai/v1/conversations/c1/items").mock(
        return_value=items(
            msg("user", ("input_text", "first")),
            msg("user", ("input_text", LINE + "\nsecond")),
        )
    )
    texts, registration = await read_text(http, "c1")
    assert (texts, registration) == ([("user", "first"), ("user", "second")], "SPARK")


async def test_an_attachment_only_conversation_gives_no_text(mock, http):
    mock.get("/openai/v1/conversations/c1/items").mock(
        return_value=items({"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "x"}]})
    )
    assert await read_text(http, "c1") == ([], None)


async def test_items_page_in_order_and_a_failure_raises(mock, http):
    route = mock.get("/openai/v1/conversations/c1/items")
    route.side_effect = [
        httpx.Response(200, json={"data": [msg("user", ("input_text", "a"))], "last_id": "i1", "has_more": True}),
        httpx.Response(404),
    ]
    with pytest.raises(FetchError, match="404"):
        await read_text(http, "c1")
    assert route.calls[0].request.url.params["order"] == "asc"


def test_nothing_that_identifies_reaches_the_log(caplog, capsys):
    import asyncio

    import respx

    async def go():
        with respx.mock(base_url="https://p.test") as router:
            router.get("/openai/v1/conversations/conv_secret123/items").mock(return_value=items())
            async with httpx.AsyncClient(base_url="https://p.test") as client:
                await read_text(client, "conv_secret123")

    hush()
    with caplog.at_level("INFO"):
        asyncio.run(go())
    assert "conv_secret123" not in caplog.text + capsys.readouterr().err


def test_nothing_that_identifies_is_in_the_repr():
    assert "conv_secret123" not in repr(Conversation("conv_secret123", "oid-1", "car", "loop", None, DAY, False))
