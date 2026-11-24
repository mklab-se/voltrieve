"""What both stores do, the memory store and the table store on a fake table."""

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables import TableEntity, TableErrorCode, TableTransactionError

from app.model import (
    AlreadyDelivered,
    NotFound,
    Offer,
    OfferExpired,
    OutOfStock,
    SameTurn,
    clean_address,
    new_offer_id,
)
from app.settings import Settings
from app.store import MemoryStore, build_store
from app.table_store import TableStore

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
STOCK = {"spark": {"Stockholm": 4, "Malmö": 1}, "atlas": {"Göteborg": 3, "Uppsala": 0}}


def failure(status: int, code: str, message: str = "failed", error=HttpResponseError) -> HttpResponseError:
    exc = error(message=message)
    exc.status_code, exc.error_code = status, code
    return exc


class FakeTable:
    """Table Storage as far as the store needs it: ETags, and a transaction that is whole or nothing."""

    def __init__(self):
        self.rows: dict[str, tuple[dict, str]] = {}
        self.transactions: list[list] = []
        self.error: Exception | None = None  # raised by every call
        self.before_commit = None  # runs once, between a read and the write that follows
        self.conflicts = 0  # this many transactions fail with 412
        self.closed = False
        self._version = 0

    def put(self, row: dict) -> None:
        self._version += 1
        self.rows[row["RowKey"]] = (dict(row), f"v{self._version}")

    def entity(self, key: str) -> TableEntity:
        row, etag = self.rows[key]
        entity = TableEntity(row)
        entity._metadata = {"etag": etag}
        return entity

    async def get_entity(self, partition_key, row_key, **_):
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        assert partition_key == "voltrieve"
        if row_key not in self.rows:
            raise failure(404, TableErrorCode.RESOURCE_NOT_FOUND, error=ResourceNotFoundError)
        return self.entity(row_key)

    async def query_entities(self, query_filter, **_):
        found = re.fullmatch(
            r"PartitionKey eq 'voltrieve' and RowKey ge '(.+)' and RowKey lt '(.+)'", query_filter
        )
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        for key in sorted(self.rows):
            if found[1] <= key < found[2]:
                yield self.entity(key)

    async def submit_transaction(self, operations, **_):
        operations = list(operations)
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        if self.before_commit:
            change, self.before_commit = self.before_commit, None
            await change()
        if self.conflicts:
            self.conflicts -= 1
            raise failure(412, "UpdateConditionNotSatisfied", error=TableTransactionError)
        keys = [operation[1]["RowKey"] for operation in operations]
        assert 1 <= len(operations) <= 100 and len(set(keys)) == len(keys)
        assert all(operation[1]["PartitionKey"] == "voltrieve" for operation in operations)
        for name, row, *options in operations:
            self._check(name, row["RowKey"], options[0] if options else {})
        for name, row, *_options in operations:
            if name == "delete":
                del self.rows[row["RowKey"]]
            else:
                self.put(row)
        self.transactions.append(operations)

    def _check(self, name: str, key: str, options: dict) -> None:
        exists = key in self.rows
        if name == "create" and exists:
            raise failure(409, TableErrorCode.ENTITY_ALREADY_EXISTS, error=TableTransactionError)
        if name in ("update", "delete") and not exists:
            raise failure(404, TableErrorCode.RESOURCE_NOT_FOUND, error=TableTransactionError)
        if "etag" in options and options["etag"] != self.rows[key][1]:
            raise failure(412, "UpdateConditionNotSatisfied", error=TableTransactionError)

    async def close(self):
        self.closed = True


@pytest.fixture(params=["memory", "table"])
async def store(request):
    made = MemoryStore() if request.param == "memory" else TableStore(FakeTable())
    await made.seed(STOCK)
    yield made
    await made.close()


def an_offer(**changes) -> Offer:
    offer = Offer(
        id=new_offer_id(),
        customer="customer-1",
        name="Ada Exempel",
        model="spark",
        price_sek=289000,
        address="Storgatan 1, 211 34 Malmö",
        turn="turn-1",
        client="web",
        created=NOW,
        order=None,
    )
    return replace(offer, **changes)


async def an_order(store, now=NOW, **changes):
    offer = an_offer(created=now, **changes)
    await store.save_offer(offer)
    order, _ = await store.place(offer.id, offer.customer, "turn-2", now)
    return offer, order


async def test_seed_only_when_there_is_no_stock(store):
    assert await store.stock() == STOCK
    await store.seed({"spark": {"Stockholm": 99}})
    assert await store.stock() == STOCK


async def test_the_seeded_stock_is_a_copy(store):
    await store.change_stock("spark", "Stockholm", 1)
    assert STOCK["spark"]["Stockholm"] == 4
    (await store.stock())["spark"]["Stockholm"] = 99
    assert (await store.stock())["spark"]["Stockholm"] == 5


async def test_stock_never_goes_below_zero(store):
    assert await store.change_stock("spark", "Malmö", -1) == 0
    with pytest.raises(OutOfStock):
        await store.change_stock("spark", "Malmö", -1)
    assert await store.change_stock("spark", "Malmö", 1) == 1
    assert (await store.stock())["spark"]["Malmö"] == 1


async def test_changes_at_once_are_all_counted(store):
    await asyncio.gather(*(store.change_stock("spark", "Stockholm", 1) for _ in range(3)))
    assert (await store.stock())["spark"]["Stockholm"] == 7


@pytest.mark.parametrize("model, city", [("nope", "Malmö"), ("spark", "Lund"), ("a/b", "c#d"), ("spark|Malmö", "x")])
async def test_unknown_stock_is_not_made(store, model, city):
    with pytest.raises(NotFound):
        await store.change_stock(model, city, 1)
    assert await store.stock() == STOCK


async def test_an_offer_becomes_one_order(store):
    offer = an_offer()
    await store.save_offer(offer)
    first, new = await store.place(offer.id, "customer-1", "turn-2", NOW)
    again, new_again = await store.place(offer.id, "customer-1", "turn-3", NOW)
    assert (first.number, new, new_again) == ("VO-1001", True, False)
    assert again == first and await store.orders() == [first]
    assert (first.model, first.price_sek, first.client) == ("spark", 289000, "web")
    assert (first.name, first.address) == (offer.name, offer.address)
    assert (first.status, first.created) == ("open", NOW)
    assert (first.city, first.delivered, first.delivered_by) == (None, None, None)


async def test_an_offer_cannot_be_placed_in_its_own_turn(store):
    offer = an_offer()
    await store.save_offer(offer)
    with pytest.raises(SameTurn):
        await store.place(offer.id, "customer-1", "turn-1", NOW)
    assert await store.orders() == []


async def test_an_offer_is_for_its_customer(store):
    offer = an_offer()
    await store.save_offer(offer)
    for offer_id, customer in [(offer.id, "customer-2"), ("unknown", "customer-1"), (f"x/{offer.id}#?", "customer-1")]:
        with pytest.raises(NotFound):
            await store.place(offer_id, customer, "turn-2", NOW)
    await store.place(offer.id, "customer-1", "turn-2", NOW)
    with pytest.raises(NotFound):
        await store.place(offer.id, "customer-2", "turn-2", NOW)


async def test_an_offer_expires_after_30_minutes(store):
    placed, late = an_offer(), an_offer()
    await store.save_offer(placed)
    await store.save_offer(late)
    order, _ = await store.place(placed.id, "customer-1", "turn-2", NOW + timedelta(minutes=29, seconds=59))
    half_past = NOW + timedelta(minutes=30)
    with pytest.raises(OfferExpired):
        await store.place(late.id, "customer-1", "turn-2", half_past)
    assert await store.place(placed.id, "customer-1", "turn-2", half_past) == (order, False)
    await store.purge(half_past)
    assert await store.place(placed.id, "customer-1", "turn-2", half_past) == (order, False)


async def test_the_next_offer_removes_the_expired_ones(store):
    old = an_offer()
    await store.save_offer(old)
    await store.save_offer(an_offer(created=NOW + timedelta(minutes=30)))
    with pytest.raises(NotFound):  # gone, not just expired
        await store.place(old.id, "customer-1", "turn-2", NOW)


async def test_delivery_takes_one_car_from_the_city(store):
    _, order = await an_order(store)
    later = NOW + timedelta(hours=1)
    done = await store.deliver(order.number, "Stockholm", "Bo Exempel", later)
    assert (done.status, done.city, done.delivered, done.delivered_by) == (
        "delivered", "Stockholm", later, "Bo Exempel",
    )
    assert await store.orders() == [done]
    assert await store.stock() == {**STOCK, "spark": {"Stockholm": 3, "Malmö": 1}}


async def test_delivery_twice_is_refused(store):
    _, order = await an_order(store)
    await store.deliver(order.number, "Stockholm", "Bo", NOW)
    with pytest.raises(AlreadyDelivered):
        await store.deliver(order.number, "Stockholm", "Bo", NOW)
    assert (await store.stock())["spark"]["Stockholm"] == 3


async def test_a_double_click_delivers_once(store):
    _, order = await an_order(store)
    results = await asyncio.gather(
        *(store.deliver(order.number, "Stockholm", "Bo", NOW) for _ in range(2)), return_exceptions=True
    )
    assert sorted(type(r).__name__ for r in results) == ["AlreadyDelivered", "Order"]
    assert (await store.stock())["spark"]["Stockholm"] == 3


@pytest.mark.parametrize("city", ["Uppsala", "Lund", "Malmö/#?"])
async def test_delivery_without_stock_changes_nothing(store, city):
    _, order = await an_order(store, model="atlas")
    with pytest.raises(OutOfStock):
        await store.deliver(order.number, city, "Bo", NOW)
    assert await store.orders() == [order] and order.status == "open"
    assert await store.stock() == STOCK


async def test_an_unknown_order(store):
    for number in ("VO-9999", "", "../x#?", "VO-1001\n"):
        with pytest.raises(NotFound):
            await store.deliver(number, "Stockholm", "Bo", NOW)
        with pytest.raises(NotFound):
            await store.delete(number)


async def test_two_deliveries_of_the_last_car(store):
    orders = [(await an_order(store))[1] for _ in range(2)]
    results = await asyncio.gather(
        *(store.deliver(o.number, "Malmö", "Bo", NOW) for o in orders), return_exceptions=True
    )
    assert sorted(type(r).__name__ for r in results) == ["Order", "OutOfStock"]
    assert (await store.stock())["spark"]["Malmö"] == 0
    assert sorted(o.status for o in await store.orders()) == ["delivered", "open"]


async def test_two_placements_at_once_get_different_numbers(store):
    offers = [an_offer(), an_offer()]
    for offer in offers:
        await store.save_offer(offer)
    placed = await asyncio.gather(*(store.place(o.id, "customer-1", "turn-2", NOW) for o in offers))
    assert {order.number for order, _ in placed} == {"VO-1001", "VO-1002"}
    assert all(new for _, new in placed)


async def test_one_offer_placed_twice_at_once_is_one_order(store):
    offer = an_offer()
    await store.save_offer(offer)
    placed = await asyncio.gather(*(store.place(offer.id, "customer-1", "turn-2", NOW) for _ in range(2)))
    assert sorted(new for _, new in placed) == [False, True]
    assert placed[0][0] == placed[1][0] and len(await store.orders()) == 1


async def test_orders_are_newest_first(store):
    for minute in range(3):
        await an_order(store, now=NOW + timedelta(minutes=minute))
    assert [o.number for o in await store.orders()] == ["VO-1003", "VO-1002", "VO-1001"]


async def test_delete_leaves_the_stock_alone(store):
    _, open_order = await an_order(store)
    _, delivered = await an_order(store)
    await store.deliver(delivered.number, "Stockholm", "Bo", NOW)
    stock = await store.stock()
    assert await store.delete(open_order.number) == open_order
    assert (await store.delete(delivered.number)).status == "delivered"
    assert await store.orders() == [] and await store.stock() == stock


async def test_an_offer_goes_with_its_order(store):
    """The offer has the name and the address too, and its order is never placed a second time."""
    offer, order = await an_order(store)
    await store.delete(order.number)
    with pytest.raises(NotFound):
        await store.place(offer.id, "customer-1", "turn-3", NOW)
    assert await store.orders() == []


async def test_a_placed_offer_has_no_name_and_no_address(store):
    """It only points at the order, so the customer's data is in one row."""
    offer, order = await an_order(store)
    if isinstance(store, MemoryStore):
        kept = vars(store._offers[offer.id])
    else:
        kept = store._client.rows[f"offer|{offer.id}"][0]
    assert (kept["name"], kept["address"], kept["order"]) == ("", "", order.number)
    assert (await store.orders())[0].address == offer.address


async def test_reset_restores_the_stock_and_keeps_the_numbering(store):
    offer, order = await an_order(store)
    unplaced = an_offer()
    await store.save_offer(unplaced)
    await store.deliver(order.number, "Stockholm", "Bo", NOW)
    await store.change_stock("atlas", "Uppsala", 1)
    for _ in range(2):  # safe to repeat
        await store.reset(STOCK)
        assert await store.stock() == STOCK and await store.orders() == []
    for gone in (offer, unplaced):
        with pytest.raises(NotFound):
            await store.place(gone.id, "customer-1", "turn-2", NOW)
    assert (await an_order(store))[1].number == "VO-1002"


async def test_reset_removes_stock_that_is_not_in_the_list(store):
    await store.reset({"spark": {"Stockholm": 2}})
    assert await store.stock() == {"spark": {"Stockholm": 2}}


async def test_purge_removes_orders_after_30_days_and_offers_after_30_minutes(store):
    _, old = await an_order(store)
    fresh_offer, fresh = await an_order(store, now=NOW + timedelta(minutes=1))
    month = timedelta(days=30)
    waiting = an_offer(created=NOW + month - timedelta(minutes=29))
    expired = an_offer(created=NOW + month - timedelta(minutes=30))
    await store.purge(NOW + month - timedelta(seconds=1))
    assert await store.orders() == [fresh, old]
    await store.save_offer(waiting)
    await store.save_offer(expired)
    for _ in range(2):  # safe to repeat
        await store.purge(NOW + month)
        assert await store.orders() == [fresh]
    with pytest.raises(NotFound):
        await store.place(expired.id, "customer-1", "turn-2", NOW + month)
    assert await store.place(fresh_offer.id, "customer-1", "turn-2", NOW + month) == (fresh, False)
    assert (await store.place(waiting.id, "customer-1", "turn-2", NOW + month))[1]
    assert await store.stock() == STOCK


def test_clean_address():
    assert clean_address("  Storgatan 1\r\n\t211 34\x00\x1b\u2028Malmö \u202e\n") == "Storgatan 1 211 34 Malmö"
    assert clean_address("Gatan 1\ud800") == "Gatan 1"
    assert clean_address("<script>alert(1)</script>") == "<script>alert(1)</script>"
    assert clean_address("a" * 5) == "aaaaa" and len(clean_address("a" * 200)) == 200
    for refused in ("", "aaaa", "aaaa\n\n\n", " " * 50, "a" * 201, "a" * 5000):
        with pytest.raises(ValueError) as raised:
            clean_address(refused)
        assert "aaa" not in str(raised.value)


async def test_an_offer_and_an_order_do_not_print_personal_data(store):
    offer, order = await an_order(store)
    for text in (repr(offer), str(order), f"{[offer, order]}"):
        assert "Ada" not in text and "Storgatan" not in text and "customer-1" not in text


def settings(**changes) -> Settings:
    return Settings(tenant_id="t", dealer_client_id="c", **changes)


def test_the_memory_store_is_only_used_when_asked_for(monkeypatch):
    for name in ("STORE", "TABLE_ENDPOINT", "TABLE_NAME"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(build_store(settings(store="memory")), MemoryStore)
    with pytest.raises(ValueError, match="TABLE_ENDPOINT"):
        build_store(settings())
    with pytest.raises(ValueError):
        settings(store="Memory")
