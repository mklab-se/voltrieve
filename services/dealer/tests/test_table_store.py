"""What only the table store has: transactions, failures, and what it sends."""

import logging
import re
import traceback
from datetime import timedelta

import pytest
from aiohttp import web
from azure.core.credentials import AzureSasCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.core.pipeline.policies import DistributedTracingPolicy
from azure.core.settings import settings as azure_settings
from azure.core.tracing.ext.opentelemetry_span import OpenTelemetrySpan
from azure.data.tables import TableErrorCode
from test_store import NOW, STOCK, FakeTable, an_offer, an_order, failure, no_wait, settings

from app.model import NotFound, StoreUnavailable
from app.store import build_store
from app.table_store import RETRIES, QuietTableClient, TableStore

MARKER = "Hemliga gatan 7"


@pytest.fixture
async def table():
    return FakeTable()


@pytest.fixture
async def store(table):
    made = TableStore(table, wait=no_wait)
    await made.seed(STOCK)
    return made


def calls(store):
    return {
        "seed": lambda: store.seed(STOCK),
        "purge": lambda: store.purge(NOW),
        "stock": lambda: store.stock(),
        "change_stock": lambda: store.change_stock("spark", "Malmö", 1),
        "save_offer": lambda: store.save_offer(an_offer(address=MARKER)),
        "place": lambda: store.place("a" * 12, "customer-1", "turn-2", NOW),
        "orders": lambda: store.orders(),
        "deliver": lambda: store.deliver("VO-1001", "Malmö", "Bo", NOW),
        "delete": lambda: store.delete("VO-1001"),
        "reset": lambda: store.reset(STOCK),
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
async def test_a_conflict_is_tried_again_five_times(store, table, caplog, name):
    waits = []

    async def wait():
        waits.append(1)

    store._wait = wait
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
    assert len(waits) == 5  # between the tries
    assert [r.getMessage() for r in caplog.records] == ["The store failed: TableTransactionError, status 412"]
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


@pytest.mark.parametrize("code", [TableErrorCode.TABLE_NOT_FOUND, "Something", None])
async def test_only_a_missing_row_is_a_conflict_among_the_404s(store, table, code):
    sent = []

    async def submit_transaction(operations, **_):
        sent.append(operations)
        raise failure(404, code)

    table.submit_transaction = submit_transaction
    with pytest.raises(StoreUnavailable):
        await store.change_stock("spark", "Malmö", 1)
    assert len(sent) == 1


async def test_a_row_that_is_gone_is_a_conflict(store, table):
    async def vanish():
        del table.rows["stock|spark|Malmö"]

    table.before_commit = vanish
    with pytest.raises(NotFound):
        await store.change_stock("spark", "Malmö", 1)


async def test_the_default_wait_is_short(monkeypatch):
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.table_store.asyncio.sleep", sleep)
    store = TableStore(FakeTable())
    for _ in range(50):
        await store._wait()
    assert 0 < min(slept) < max(slept) <= 0.2


@pytest.mark.parametrize("error, reads", [(ERRORS["busy"], 2), (ERRORS["unreachable"], 2), (ERRORS["forbidden"], 1)])
async def test_a_failed_read_is_repeated_once(store, table, error, reads):
    table.error, count, get = error, 0, table.get_entity

    async def counted(*args, **options):
        nonlocal count
        count += 1
        return await get(*args, **options)

    table.get_entity = counted
    with pytest.raises(StoreUnavailable):
        await store.change_stock("spark", "Malmö", 1)
    assert count == reads


BAD_OFFERS = ["", "a" * 11, "a" * 13, "a" * 11 + "/", "a" * 11 + "å", "a" * 12 + "\n", "a" * 5000, "Storgatan 12"]
BAD_NUMBERS = ["", "1001", "VO-", "VO-1x", "vo-1001", "VO-1001\n", "VO-" + "1" * 10, "VO-١٠٠١", "VO-1001|x"]


async def test_what_is_not_an_id_is_never_sent(store, table):
    table.error = AssertionError("a request was made")
    for offer in BAD_OFFERS:
        with pytest.raises(NotFound):
            await store.place(offer, "customer-1", "turn-2", NOW)
    for number in BAD_NUMBERS:
        with pytest.raises(NotFound):
            await store.delete(number)
        with pytest.raises(NotFound):
            await store.deliver(number, "Malmö", "Bo", NOW)


DAMAGE = {
    "no price": ("order|VO-1001", {"price_sek": None}),
    "price in words": ("order|VO-1001", {"price_sek": MARKER}),
    "no address": ("order|VO-1001", {"address": None}),
    "a bad time": ("order|VO-1001", {"created": MARKER}),
    "a time without a zone": ("order|VO-1001", {"created": "2026-11-24T12:00:00"}),
    "a bad number": ("order|VO-1001", {"number": MARKER}),
    "a bad offer": ("offer|x", {"created": MARKER, "order": None}),
    "stock in words": ("stock|spark|Malmö", {"in_stock": MARKER}),
    "no stock": ("stock|spark|Malmö", {"in_stock": None}),
    "no city": ("stock|spark|Malmö", {"city": None}),
    "a bad counter": ("counter", {"next": MARKER}),
}


@pytest.mark.parametrize("damage", DAMAGE)
async def test_a_damaged_row_is_store_unavailable(store, table, caplog, damage):
    offer, _ = await an_order(store)
    waiting = an_offer()
    await store.save_offer(waiting)
    key, changes = DAMAGE[damage]
    key = f"offer|{offer.id}" if key == "offer|x" else key
    table.put({k: v for k, v in {**table.rows[key][0], **changes}.items() if v is not None})
    failed = 0
    for name, call in {**calls(store), "place": lambda: store.place(waiting.id, "customer-1", "turn-2", NOW)}.items():
        if name == "reset":  # it reads no values, so it can repair
            continue
        try:
            await call()
        except StoreUnavailable as exc:
            failed += 1
            assert exc.__cause__ is None and exc.__context__ is None
            assert MARKER not in "".join(traceback.format_exception(exc))
        except Exception as exc:  # what the rules say, never an error in the code
            assert type(exc).__module__ == "app.model"
    assert failed and MARKER not in caplog.text
    await store.reset(STOCK)
    assert await store.stock() == STOCK and await store.orders() == []


async def test_a_damaged_order_is_not_deleted_by_halves(store, table):
    await an_order(store)
    table.put({**table.rows["order|VO-1001"][0], "created": MARKER})
    before = dict(table.rows)
    with pytest.raises(StoreUnavailable):
        await store.delete("VO-1001")
    assert table.rows == before


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


ETAG = "W/\"datetime'2026'\""
CONFLICT = '{"odata.error": {"code": "UpdateConditionNotSatisfied", "message": {"lang": "en", "value": "0:no"}}}'


def parts(*answers: str) -> web.Response:
    """The answer to a transaction: one part for each operation, or one for the one that failed."""
    part = "--changeset_1\r\nContent-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n{}\r\n"
    inner = "".join(part.format(answer) for answer in answers) + "--changeset_1--\r\n"
    body = f"--batch_1\r\nContent-Type: multipart/mixed; boundary=changeset_1\r\n\r\n{inner}--batch_1--\r\n"
    return web.Response(status=202, body=body.encode(), headers={"Content-Type": "multipart/mixed; boundary=batch_1"})


def saved() -> web.Response:
    return parts(f"HTTP/1.1 204 No Content\r\nETag: {ETAG}\r\n\r\n")


def conflict() -> web.Response:
    return parts(f"HTTP/1.1 412 Precondition Failed\r\nContent-Type: application/json\r\n\r\n{CONFLICT}")


class Service:
    """A table service on this machine, for the real SDK. `gets` and `posts` are the next answers."""

    def __init__(self):
        self.seen, self.gets, self.posts = [], [], []

    async def answer(self, request: web.Request):
        self.seen.append((request.method, request.raw_path, await request.text()))
        planned = self.posts if request.method == "POST" else self.gets
        if planned:
            return planned.pop(0)(request)
        if request.method == "POST":
            return saved()
        path = request.raw_path
        row = {"PartitionKey": "voltrieve", "odata.etag": ETAG}
        if "VO-1002" in path:
            order = {"number": "VO-1002", "model": "spark", "price_sek": 1, "name": "n", "address": "a", "client": "web"}
            created = {"status": "open", "created": NOW.isoformat(), "offer": "b" * 12}
            return web.json_response({**row, "RowKey": "order|VO-1002", **order, **created})
        if "VO-" in path or "offer" in path:
            error = {"odata.error": {"code": "ResourceNotFound", "message": {"lang": "en", "value": "no"}}}
            return web.json_response(error, status=404)
        row |= {"RowKey": "stock|spark|Malmö", "model": "spark", "city": "Malmö", "in_stock": 2}
        return web.json_response(row if "PartitionKey=" in path else {"value": [row]})

    def methods(self):
        return [method for method, *_ in self.seen]


def busy(_):
    return web.Response(status=503)


def redirect(request):
    return web.Response(status=307, headers={"Location": str(request.url.with_query({"again": "1"}))})


def drop(request):
    request.transport.close()
    return web.Response()


@pytest.fixture
async def service():
    made = Service()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", made.answer)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    endpoint = f"http://127.0.0.1:{runner.addresses[0][1]}"
    client = QuietTableClient(endpoint, "dealer", credential=AzureSasCredential("sig=test"), retry_backoff_factor=0)
    made.store = TableStore(client, wait=no_wait)
    yield made
    await made.store.close()
    await runner.cleanup()


@pytest.fixture
def spans(monkeypatch):
    """The spans that the SDK starts."""
    started, start = [], OpenTelemetrySpan.__init__

    def counted(self, *args, **named):
        started.append(named)
        start(self, *args, **named)

    monkeypatch.setattr(OpenTelemetrySpan, "__init__", counted)
    azure_settings.tracing_implementation = OpenTelemetrySpan
    yield started
    azure_settings.tracing_implementation.unset_value()


async def test_keys_are_encoded_by_the_sdk(service):
    assert await service.store.stock() == {"spark": {"Malmö": 2}}
    assert await service.store.change_stock("spark", "Malmö", 1) == 3
    read, (_, path, body) = service.seen[1], service.seen[2]
    key = "(PartitionKey='voltrieve',RowKey='stock%7Cspark%7CMalm%C3%B6')"
    assert f"/dealer{key}" in read[1] and path.startswith("/$batch")
    assert re.search(rf"PUT http://127.0.0.1:\d+/dealer{re.escape(key)}", body)
    assert f"If-Match: {ETAG}" in body and '"in_stock": 3' in body


async def test_a_delete_has_the_etag_of_what_was_read(service):
    assert (await service.store.delete("VO-1002")).number == "VO-1002"
    (body,) = [body for method, _, body in service.seen if method == "POST"]
    assert re.search(r"DELETE http://127.0.0.1:\d+/dealer\(.*RowKey='order%7CVO-1002'\)", body)
    assert f"If-Match: {ETAG}" in body


async def test_a_conflict_of_the_real_sdk_is_read_and_tried_again(service):
    service.posts = [lambda _: conflict()]
    assert await service.store.change_stock("spark", "Malmö", 1) == 3
    assert service.methods() == ["GET", "POST", "GET", "POST"]


async def test_a_write_is_sent_once(service):
    """The SDK would send it again after a lost answer, and then one click could count twice."""
    service.posts = [busy]
    with pytest.raises(StoreUnavailable):
        await service.store.change_stock("spark", "Malmö", 1)
    assert service.methods() == ["GET", "POST"]


@pytest.mark.parametrize("answer", [busy, drop, redirect])
async def test_a_call_that_is_sent_again_makes_no_span(service, spans, answer):
    """A span has the key in its URL, and the message of the SDK when the call failed."""
    service.gets = [answer]
    assert await service.store.change_stock("spark", "Malmö", 1) == 3
    assert service.methods() == ["GET", "GET", "POST"]
    service.gets = [answer]
    assert await service.store.stock() == {"spark": {"Malmö": 2}}
    assert spans == []


async def test_a_call_that_fails_makes_no_span(service, spans):
    service.gets = [busy, busy]
    with pytest.raises(StoreUnavailable):
        await service.store.stock()
    service.gets = [drop] * 4  # aiohttp can send a GET again by itself
    with pytest.raises(StoreUnavailable):
        await service.store.change_stock("spark", "Malmö", 1)
    service.gets, service.posts = [], [busy, drop]
    for _ in range(2):
        with pytest.raises(StoreUnavailable):
            await service.store.change_stock("spark", "Malmö", 1)
    with pytest.raises(NotFound):
        await service.store.delete("VO-1001")
    assert spans == []


async def test_the_span_fixture_sees_a_span(service, spans):
    """So the tests above are not empty."""
    with pytest.raises(HttpResponseError):
        await service.store._client.get_entity("voltrieve", "order|VO-1001")
    assert spans


def test_the_client_in_azure_has_no_tracing_in_its_pipeline():
    """A call is also sent again after a 401 for an old token, where the options of the call are used up."""
    made = build_store(settings(table_endpoint="https://example.table.core.windows.net"))
    policies = made._client._policies
    assert policies and not [policy for policy in policies if isinstance(policy, DistributedTracingPolicy)]


async def test_a_missing_row_is_told_from_a_missing_table(service):
    with pytest.raises(NotFound) as raised:
        await service.store.delete("VO-1001")
    assert raised.value.__context__ is None
