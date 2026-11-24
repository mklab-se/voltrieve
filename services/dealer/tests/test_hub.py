import asyncio
from datetime import UTC, datetime

from app.hub import LOG_SIZE, QUEUE_SIZE, Hub

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


async def test_an_entry_has_every_field():
    hub = Hub(lambda: NOW)
    hub.add("prices", "Runa")
    more = {"model": "spark", "city": "Malmö", "number": "VO-1001", "result": "r", "correlation_id": "c"}
    hub.add("order", "Runa", client="web", **more)
    first, second = hub.latest()
    assert first == {
        "id": 1, "at": "2026-11-24T12:00:00+00:00", "kind": "prices", "who": "Runa", "client": None,
        "model": None, "city": None, "number": None, "result": None, "correlation_id": None,
    }  # fmt: skip
    assert second == {
        "id": 2, "at": "2026-11-24T12:00:00+00:00", "kind": "order", "who": "Runa", "client": "web",
        "model": "spark", "city": "Malmö", "number": "VO-1001", "result": "r", "correlation_id": "c",
    }  # fmt: skip


async def test_it_keeps_the_latest_200():
    hub = Hub()
    for _ in range(LOG_SIZE + 5):
        hub.add("prices", "Runa")
    kept = [entry["id"] for entry in hub.latest()]
    assert LOG_SIZE == 200 and kept == list(range(6, 206))


async def test_every_subscriber_gets_every_item_in_order():
    hub = Hub()
    hub.add("prices", "Runa")  # before they subscribed
    queues = [hub.subscribe(), hub.subscribe()]
    hub.add("stock", "Runa")
    hub.changed()
    hub.clear()
    for queue in queues:
        items = [queue.get_nowait() for _ in range(queue.qsize())]
        assert [name for name, _ in items] == ["log", "changed", "clear"]
        assert items[0][1]["kind"] == "stock" and items[1:] == [("changed", {}), ("clear", {})]


async def test_clear_empties_the_log_and_the_ids_go_on():
    hub = Hub()
    hub.add("prices", "Runa")
    hub.clear()
    assert hub.latest() == []
    hub.add("prices", "Runa")
    assert hub.latest()[0]["id"] == 2


async def test_an_unsubscribed_queue_gets_nothing():
    hub = Hub()
    queue = hub.subscribe()
    hub.unsubscribe(queue)
    hub.unsubscribe(queue)  # a second time is fine
    hub.changed()
    assert queue.empty()


async def test_a_slow_subscriber_is_dropped_and_does_not_stop_the_others():
    hub = Hub()
    slow, fast = hub.subscribe(), hub.subscribe()
    got = 0
    for _ in range(QUEUE_SIZE + 3):
        hub.changed()
        fast.get_nowait()
        got += 1
    assert got == QUEUE_SIZE + 3
    assert slow.qsize() == QUEUE_SIZE
    assert not hub.subscribed(slow) and hub.subscribed(fast)
    while not slow.empty():
        slow.get_nowait()
    hub.changed()
    assert slow.empty()


async def test_a_waiting_subscriber_wakes_up():
    hub = Hub()
    queue = hub.subscribe()
    waiting = asyncio.create_task(queue.get())
    await asyncio.sleep(0)
    hub.changed()
    assert await asyncio.wait_for(waiting, 1) == ("changed", {})


async def test_the_caller_cannot_change_the_log():
    hub = Hub()
    hub.add("prices", "Runa")
    hub.latest().clear()
    assert len(hub.latest()) == 1
