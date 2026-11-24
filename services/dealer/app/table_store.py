"""The store in Table Storage. All rows are in one partition, so a transaction can hold any of them.

A row, a key or a message from the SDK never leaves this module. An error holds fixed words, the log has its
type and status, and the calls make no spans: a span has the key in its URL, and the message of a failed call.
"""

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import fields
from datetime import datetime

from azure.core import MatchConditions
from azure.core.pipeline.policies import DistributedTracingPolicy, SansIOHTTPPolicy
from azure.data.tables import TableEntity, TableErrorCode, UpdateMode
from azure.data.tables.aio import TableClient
from azure.identity.aio import DefaultAzureCredential

from app.model import (
    FIRST_NUMBER,
    NotFound,
    Offer,
    Order,
    StoreUnavailable,
    delivered,
    newest_first,
    offer_is_stale,
    order_expired,
    order_from,
    placed,
    stock_after,
)

logger = logging.getLogger(__name__)

PARTITION = "voltrieve"
COUNTER = "counter"
# The keys this service can have made. Nothing else is sent.
KEY = re.compile(
    r"counter|offer\|[A-Za-z0-9_-]{12}|order\|VO-[0-9]{1,9}|stock\|[\w. -]{1,50}\|[\w. -]{1,50}"
)
RETRIES = 5  # after a conflict
TRANSACTION = 100  # operations, the limit of Table Storage
# The SDK never sends a call again: we do, so that each is sent with these options.
CALL = {"retry_total": 0, "tracing_options": {"enabled": False}}


def open_table_store(endpoint: str, table: str) -> "TableStore":
    credential = DefaultAzureCredential(managed_identity_client_id=None)  # the system-assigned identity
    client = QuietTableClient(endpoint, table, credential=credential, connection_timeout=5, read_timeout=10)
    return TableStore(client, credential)


class _NoSpans(SansIOHTTPPolicy):
    def on_request(self, request) -> None:
        request.context.options.pop("tracing_options", None)


class QuietTableClient(TableClient):
    """The policy of the SDK forgets `tracing_options` after the first send, and a call can be sent again after
    a redirect or a 401 for an old token. So the policy is not in the pipeline at all."""

    def _configure_policies(self, **kwargs) -> list:
        policies = super()._configure_policies(**kwargs)
        return [_NoSpans() if isinstance(policy, DistributedTracingPolicy) else policy for policy in policies]


async def _short_wait() -> None:
    await asyncio.sleep(random.uniform(0.02, 0.2))


class TableStore:
    def __init__(
        self,
        client: TableClient,
        credential: DefaultAzureCredential | None = None,
        wait: Callable[[], Awaitable[None]] = _short_wait,
    ):
        self._client = client
        self._credential = credential
        self._wait = wait  # after a conflict

    async def seed(self, stock: dict[str, dict[str, int]]) -> None:
        async def attempt():
            if not await self._rows("stock"):
                await self._commit([("create", row) for row in _stock_rows(stock)])

        await self._until_done(attempt)

    async def stock(self) -> dict[str, dict[str, int]]:
        async def attempt():
            found: dict[str, dict[str, int]] = {}
            for row in await self._rows("stock"):
                found.setdefault(row["model"], {})[row["city"]] = _count(row, "in_stock")
            return found

        return await self._until_done(attempt)

    async def change_stock(self, model: str, city: str, change: int) -> int:
        async def attempt():
            row = await self._get("stock", model, city)
            if row is None:
                raise NotFound
            left = stock_after(_count(row, "in_stock"), change)
            await self._commit([_update(row, in_stock=left)])
            return left

        return await self._until_done(attempt)

    async def save_offer(self, offer: Offer) -> None:
        await self._until_done(lambda: self._commit([("create", _row(offer, "offer", offer.id))]))

    async def place(self, offer_id: str, customer: str, turn: str, now: datetime) -> tuple[Order, bool]:
        async def attempt():
            offer_row = await self._get("offer", offer_id)
            offer = offer_row and _read(Offer, offer_row)
            if offer is None or offer.customer != customer:
                raise NotFound
            if offer.order:
                return _read(Order, await self._order(offer.order)), False
            counter = await self._get(COUNTER)
            number = _count(counter, "next") if counter else FIRST_NUMBER
            order = order_from(offer, number, turn, now)
            counting = _update(counter, next=number + 1) if counter else ("create", _new(COUNTER, next=number + 1))
            order_row = {**_row(order, "order", order.number), "offer": offer.id}
            offer_update = _update(offer_row, **_row(placed(offer, order.number)))
            await self._commit([counting, ("create", order_row), offer_update])
            return order, True

        return await self._until_done(attempt)

    async def orders(self) -> list[Order]:
        async def attempt():
            return newest_first([_read(Order, row) for row in await self._rows("order")])

        return await self._until_done(attempt)

    async def deliver(self, number: str, city: str, by: str, now: datetime) -> Order:
        async def attempt():
            row = await self._order(number)
            stock = await self._get("stock", row["model"], city)
            in_stock = _count(stock, "in_stock") if stock else 0
            order = delivered(_read(Order, row), in_stock, city, by, now)
            await self._commit([_update(row, **_row(order)), _update(stock, in_stock=in_stock - 1)])
            return order

        return await self._until_done(attempt)

    async def delete(self, number: str) -> Order:
        async def attempt():
            row = await self._order(number)
            order = _read(Order, row)
            offer = await self._get("offer", row["offer"])
            await self._commit([_delete(found) for found in (row, offer) if found])
            return order

        return await self._until_done(attempt)

    async def reset(self, stock: dict[str, dict[str, int]]) -> None:
        """Safe to repeat. The stock is last, so a delivery in between does not change it."""

        async def attempt():
            await self._commit([_delete(row) for row in await self._rows("offer", "order")])
            wanted = {row["RowKey"]: row for row in _stock_rows(stock)}
            other = [row for row in await self._rows("stock") if row["RowKey"] not in wanted]
            put = [("upsert", row, {"mode": UpdateMode.REPLACE}) for row in wanted.values()]
            await self._commit([*map(_delete, other), *put])

        await self._until_done(attempt)

    async def purge(self, now: datetime) -> None:
        """Safe to repeat. The offers are read first, so the order of a placed one is always there to be seen."""

        async def attempt():
            offers, orders = await self._rows("offer"), await self._rows("order")
            old = [row for row in orders if order_expired(_read(Order, row), now)]
            kept = {row["number"] for row in orders} - {row["number"] for row in old}
            stale = [row for row in offers if offer_is_stale(_read(Offer, row), kept, now)]
            await self._commit([_delete(row) for row in old + stale])

        await self._until_done(attempt)

    async def close(self) -> None:
        await _ask(self._client.close())
        if self._credential:
            await _ask(self._credential.close())

    async def _order(self, number: str) -> TableEntity:
        row = await self._get("order", number)
        if row is None:
            raise NotFound
        return row

    async def _get(self, *parts: str) -> TableEntity | None:
        key = "|".join(parts)
        if not KEY.fullmatch(key):
            return None
        row, failed = await _ask_twice(lambda: self._client.get_entity(PARTITION, key, **CALL))
        if failed and failed.code != TableErrorCode.RESOURCE_NOT_FOUND:
            raise failed.unavailable()
        return row

    async def _rows(self, *kinds: str) -> list[TableEntity]:
        found: list[TableEntity] = []
        for kind in kinds:
            query = f"PartitionKey eq '{PARTITION}' and RowKey ge '{kind}|' and RowKey lt '{kind}}}'"  # } is after |
            rows, failed = await _ask_twice(lambda: _all(self._client.query_entities(query, **CALL)))
            if failed:
                raise failed.unavailable()
            found += rows
        return found

    async def _commit(self, operations: list[tuple]) -> None:
        """Up to 100 operations are whole or nothing. Raises _Conflict when a row is not as it was read.

        It is sent once: sent again after a lost answer, it would look like a conflict, and then one click
        could count twice.
        """
        for start in range(0, len(operations), TRANSACTION):
            part = operations[start : start + TRANSACTION]
            _, failed = await _ask(self._client.submit_transaction(part, **CALL))
            if failed and failed.conflict:
                raise _Conflict(failed)
            if failed:
                raise failed.unavailable()

    async def _until_done[T](self, attempt: Callable[[], Awaitable[T]]) -> T:
        """Each attempt reads again. A damaged row fails it, and the error can hold the row."""
        for tries in range(1 + RETRIES):
            if tries:
                await self._wait()
            try:
                return await attempt()
            except _Conflict as conflict:
                failed = conflict.failed
            except (KeyError, TypeError, ValueError) as exc:
                failed = _Failure(exc)
                break
        raise failed.unavailable()


class _Failure:
    """What may be known of an SDK error."""

    def __init__(self, exc: Exception):
        self.type = type(exc).__name__
        self.status = getattr(exc, "status_code", None)
        self.code = getattr(exc, "error_code", None)

    @property
    def conflict(self) -> bool:
        """Another call changed or made a row, or (404) a row that was read is gone."""
        gone = self.status == 404 and self.code == TableErrorCode.RESOURCE_NOT_FOUND
        return gone or self.status in (409, 412)

    @property
    def may_pass(self) -> bool:
        return self.status is None or self.status >= 500 or self.status in (408, 429)

    def unavailable(self) -> StoreUnavailable:
        logger.warning("The store failed: %s, status %s", self.type, self.status)
        return StoreUnavailable("the store gave no answer")


class _Conflict(Exception):
    def __init__(self, failed: _Failure):
        super().__init__()
        self.failed = failed


async def _ask(call: Awaitable) -> tuple:
    """The answer, or the failure. The caller raises outside of the except block, so nothing is chained."""
    try:
        return await call, None
    except Exception as exc:  # whatever the SDK raises can hold a row
        return None, _Failure(exc)


async def _ask_twice(read: Callable[[], Awaitable]) -> tuple:
    answer, failed = await _ask(read())
    if failed and failed.may_pass:
        answer, failed = await _ask(read())
    return answer, failed


async def _all(rows) -> list[TableEntity]:
    return [row async for row in rows]


def _new(*parts: str, **values) -> dict:
    return {"PartitionKey": PARTITION, "RowKey": "|".join(parts), **values}


def _row(item: Offer | Order, *key: str) -> dict:
    """The properties of an item, with its key when one is given."""
    values = {name: value for name, value in vars(item).items() if value is not None}
    values = {name: value.isoformat() if isinstance(value, datetime) else value for name, value in values.items()}
    return _new(*key, **values) if key else values


def _read[T: (Offer, Order)](kind: type[T], row: TableEntity) -> T:
    values = {field.name: row.get(field.name) for field in fields(kind)}
    for name in ("created", "delivered"):
        if values.get(name):
            values[name] = datetime.fromisoformat(values[name])
    for field in fields(kind):
        if not isinstance(values[field.name], field.type):
            raise ValueError("a damaged row")
    if values["created"].tzinfo is None:
        raise ValueError("a damaged row")
    return kind(**values)


def _count(row: TableEntity, name: str) -> int:
    if not isinstance(row[name], int):
        raise ValueError("a damaged row")
    return row[name]


def _stock_rows(stock: dict[str, dict[str, int]]) -> list[dict]:
    return [
        _new("stock", model, city, model=model, city=city, in_stock=count)
        for model, by_city in stock.items()
        for city, count in by_city.items()
    ]


def _unchanged(row: TableEntity) -> dict:
    return {"etag": row.metadata["etag"], "match_condition": MatchConditions.IfNotModified}


def _update(row: TableEntity, **changes) -> tuple:
    return "update", {**row, **changes}, {"mode": UpdateMode.REPLACE, **_unchanged(row)}


def _delete(row: TableEntity) -> tuple:
    return "delete", row, _unchanged(row)
