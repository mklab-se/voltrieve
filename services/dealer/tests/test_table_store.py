"""What only the table store has: transactions, failures, and what it sends."""

import logging
import re
import traceback
from datetime import timedelta

import pytest
from aiohttp import web
from azure.core.credentials import AzureSasCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.core.settings import settings as azure_settings
from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan
from azure.data.tables import TableErrorCode
from azure.data.tables.aio import TableClient
from test_store import NOW, STOCK, FakeTable, an_offer, an_order, failure, settings

from app.model import StoreUnavailable
from app.store import build_store
from app.table_store import RETRIES, TableStore

MARKER = "Hemliga gatan 7"


@pytest.fixture
async def table():
    return FakeTable()


@pytest.fixture
async def store(table):
    made = TableStore(table)
    await made.seed(STOCK)
    return made


def calls(store):
    return {
        "seed": lambda: store.seed(STOCK),
        "stock": lambda: store.stock(),
        "change_stock": lambda: store.change_stock("spark", "Malmö", 1),
        "save_offer": lambda: store.save_offer(an_offer(address=MARKER)),
        "place": lambda: store.place("unknown", "customer-1", "turn-2", NOW),
        "orders": lambda: store.orders(),
        "deliver": lambda: store.deliver("VO-1001", "Malmö", "Bo", NOW),
        "delete": lambda: store.delete("VO-1001"),
        "reset": lambda: store.reset(STOCK),
        "purge": lambda: store.purge(NOW),
    }


async def test_the_row_keys(store, table):
    offer, order = await an_order(store)
    assert set(table.rows) == {
        "counter", f"offer|{offer.id}", f"order|{order.number}",
        "stock|spark|Stockholm", "stock|spark|Malmö", "stock|atlas|Göteborg", "stock|atlas|Uppsala",
    }  # fmt: skip


async def test_more_than_100_rows_can_be_reset(store, table):
    for _ in range(130):
        await an_order(store)
    await store.reset(STOCK)
    assert await store.orders() == [] and await store.stock() == STOCK
    assert max(len(transaction) for transaction in table.transactions) == 100
    assert not [key for key in table.rows if key.startswith(("offer|", "order|"))]
    assert (await an_order(store))[1].number == "VO-1131"


async def test_more_than_100_rows_can_be_purged(store, table):
    for _ in range(130):
        await an_order(store)
    await store.purge(NOW + timedelta(days=30))
    assert await store.orders() == [] and await store.stock() == STOCK


async def test_a_reset_that_stopped_halfway_can_be_repeated(store, table):
    for _ in range(130):
        await an_order(store)
    await store.change_stock("spark", "Malmö", 1)
    submit, count = table.submit_transaction, 0

    async def fails_at_the_second(operations, **options):
        nonlocal count
        count += 1
        if count == 2:
            raise ServiceRequestError("gone")
        await submit(operations, **options)

    table.submit_transaction = fails_at_the_second
    with pytest.raises(StoreUnavailable):
        await store.reset(STOCK)
    await store.reset(STOCK)
    assert await store.orders() == [] and await store.stock() == STOCK


async def test_an_order_that_arrives_during_a_reset_is_removed_too(store, table):
    offer = an_offer()
    await store.save_offer(offer)

    async def place():
        await store.place(offer.id, "customer-1", "turn-2", NOW)

    table.before_commit = place
    await store.reset(STOCK)
    assert await store.orders() == []


async def test_a_delivery_and_a_delete_at_once(store, table):
    _, order = await an_order(store)

    async def delete():
        await store.delete(order.number)

    table.before_commit = delete
    with pytest.raises(Exception) as raised:
        await store.deliver(order.number, "Malmö", "Bo", NOW)
    assert type(raised.value).__name__ == "NotFound"
    assert await store.stock() == STOCK


@pytest.mark.parametrize("name", ["change_stock", "place", "deliver", "delete"])
async def test_a_conflict_is_tried_again_five_times(store, table, name):
    waiting = an_offer()
    await an_order(store)
    await store.save_offer(waiting)
    call = {**calls(store), "place": lambda: store.place(waiting.id, "customer-1", "turn-2", NOW)}[name]
    before = dict(table.rows)
    assert RETRIES == 5
    table.conflicts = 6
    with pytest.raises(StoreUnavailable):
        await call()
    assert table.rows == before and table.conflicts == 0
    table.conflicts = 5
    await call()
    assert table.rows != before


NAMES = list(calls(None))
ERRORS = {
    "unreachable": ServiceRequestError(MARKER),
    "no token": ClientAuthenticationError(MARKER),
    "forbidden": failure(403, "AuthorizationFailure", MARKER),
    "busy": failure(503, "ServerBusy", MARKER),
    "no table": failure(404, TableErrorCode.TABLE_NOT_FOUND, MARKER),
    "no status": HttpResponseError(MARKER),
    "a bad value": ValueError(MARKER),
}


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("error", ERRORS.values(), ids=ERRORS)
async def test_azure_errors_become_store_unavailable(store, table, caplog, name, error):
    caplog.set_level(logging.DEBUG)
    table.error = error
    with pytest.raises(StoreUnavailable) as raised:
        await calls(store)[name]()
    exc = raised.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert MARKER not in "".join(traceback.format_exception(exc)) + caplog.text
    assert type(error).__name__ in caplog.text  # logged by type


async def test_a_failed_write_does_not_show_its_row(store, table):
    """A real SDK error can hold the row it was about."""
    offer = an_offer(address=MARKER)
    await store.save_offer(offer)

    async def submit_transaction(operations, **_):
        raise failure(400, "InvalidInput", f"bad row {operations}")

    table.submit_transaction = submit_transaction
    with pytest.raises(StoreUnavailable) as raised:
        await store.place(offer.id, "customer-1", "turn-2", NOW)
    assert raised.value.__context__ is None and MARKER not in "".join(traceback.format_exception(raised.value))


async def test_a_missing_table_is_not_a_conflict(store, table):
    sent = []

    async def submit_transaction(operations, **_):
        sent.append(operations)
        raise failure(404, TableErrorCode.TABLE_NOT_FOUND)

    table.submit_transaction = submit_transaction
    with pytest.raises(StoreUnavailable):
        await store.change_stock("spark", "Malmö", 1)
    assert len(sent) == 1


async def test_close_closes_the_client_and_the_credential(table):
    credential = FakeTable()
    await TableStore(table, credential).close()
    assert table.closed and credential.closed


def test_the_table_store_is_built_for_the_endpoint():
    made = build_store(settings(table_endpoint="https://example.table.core.windows.net", table_name="dealer"))
    assert isinstance(made, TableStore)


def test_a_table_endpoint_without_tls_is_refused():
    with pytest.raises(ValueError):
        build_store(settings(table_endpoint="http://example.table.core.windows.net"))


@pytest.fixture
async def service():
    """A table service on this machine that answers 503 to a transaction, for the real SDK."""
    seen = []
    entity = {"PartitionKey": "voltrieve", "in_stock": 2, "odata.etag": "W/\"datetime'2026'\""}

    async def answer(request: web.Request):
        seen.append((request.method, request.raw_path, await request.text()))
        if request.method == "POST":
            return web.Response(status=503)
        if "VO-" in request.raw_path:
            error = {"odata.error": {"code": "ResourceNotFound", "message": {"lang": "en", "value": "no"}}}
            return web.json_response(error, status=404)
        key = {"RowKey": "stock|spark|Malmö", "model": "spark", "city": "Malmö"}
        if "PartitionKey=" in request.raw_path:
            return web.json_response({**entity, **key})
        return web.json_response({"value": [{**entity, **key}]})

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", answer)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    endpoint = f"http://127.0.0.1:{runner.addresses[0][1]}"
    client = TableClient(endpoint, "dealer", credential=AzureSasCredential("sig=test"), retry_backoff_factor=0)
    store = TableStore(client)
    yield store, seen
    await store.close()
    await runner.cleanup()


async def test_keys_are_encoded_by_the_sdk(service):
    store, seen = service
    assert await store.stock() == {"spark": {"Malmö": 2}}
    with pytest.raises(StoreUnavailable):
        await store.change_stock("spark", "Malmö", 1)
    read, (_, path, body) = seen[1], seen[2]
    key = "(PartitionKey='voltrieve',RowKey='stock%7Cspark%7CMalm%C3%B6')"
    assert f"/dealer{key}" in read[1] and path.startswith("/$batch")
    assert re.search(rf"PUT http://127.0.0.1:\d+/dealer{re.escape(key)}", body)
    assert "If-Match: W/\"datetime'2026'\"" in body and '"in_stock": 3' in body


async def test_a_write_is_sent_once(service):
    """The SDK would send it again after a lost answer, and then one click could count twice."""
    store, seen = service
    with pytest.raises(StoreUnavailable):
        await store.change_stock("spark", "Malmö", 1)
    assert [method for method, *_ in seen] == ["GET", "POST"]


async def test_the_calls_make_no_spans(service, monkeypatch):
    """A span of a failed call would hold the message of the SDK."""
    store, seen = service
    spans = []
    monkeypatch.setattr(OpenTelemetrySpan, "__init__", lambda self, *args, **named: spans.append(named))
    azure_settings.tracing_implementation = OpenTelemetrySpan
    try:
        await store.stock()
        with pytest.raises(StoreUnavailable):
            await store.change_stock("spark", "Malmö", 1)
    finally:
        azure_settings.tracing_implementation.unset_value()
    assert len(seen) == 3 and spans == []


async def test_a_missing_row_is_told_from_a_missing_table(service):
    store, _ = service
    with pytest.raises(Exception) as raised:
        await store.delete("VO-1001")
    assert type(raised.value).__name__ == "NotFound" and raised.value.__context__ is None
