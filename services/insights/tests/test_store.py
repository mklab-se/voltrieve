import json
import traceback
from datetime import UTC, date, datetime

import httpx
import pytest
import respx

from voltrieve_insights import store
from voltrieve_insights.store import COLUMNS, MAX_BODY, StoreError, previous_names, save, table_rows

INGESTION = "https://rule.test"
SAVE_URL = f"{INGESTION}/dataCollectionRules/dcr-1/streams/Custom-VoltrieveInsights_CL?api-version=2023-01-01"
QUERY_URL = "https://api.loganalytics.azure.com/v1/workspaces/ws-1/query"
RUN = datetime(2026, 11, 24, 2, 30, 5, 123456, tzinfo=UTC)
FIRST, LAST = date(2026, 11, 16), date(2026, 11, 22)
SECRET = "planted-secret-words"
LABELS = {"Client": "", "Topic": "", "CarModel": "", "RequestKind": "", "Result": "", "GroupName": ""}
FOUND = [
    {"Cut": "run", **LABELS},
    {"Cut": "topic", **LABELS, "Topic": "charging", "Conversations": 10, "People": 5},
]


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def http(mock):
    async with httpx.AsyncClient() as client:
        yield client


def rows(source="real", share=1.0):
    return table_rows(FOUND, RUN, FIRST, LAST, source, share)


def test_every_row_has_every_column_and_no_other():
    for row in rows():
        assert list(row) == list(COLUMNS)


def test_dates_are_at_midnight_utc_and_the_run_is_a_time():
    for row in rows():
        assert row["WindowStart"] == "2026-11-16T00:00:00Z"
        assert row["WindowEnd"] == "2026-11-22T00:00:00Z"
        assert row["Run"] == row["TimeGenerated"] == "2026-11-24T02:30:05Z"


def test_a_run_row_has_null_counts_and_the_others_have_theirs():
    run, topic = rows("seeded", 0.25)
    assert run["Cut"] == "run" and run["Conversations"] is None and run["People"] is None
    assert topic["Conversations"] == 10 and topic["People"] == 5 and topic["Topic"] == "charging"
    for row in (run, topic):
        assert row["Source"] == "seeded" and row["SampleShare"] == 0.25


def test_a_share_is_a_real_number():
    assert isinstance(rows(share=1)[0]["SampleShare"], float)


def test_an_unknown_label_is_not_saved():
    with pytest.raises(StoreError):
        table_rows([{**FOUND[1], "Owner": "oid"}], RUN, FIRST, LAST, "real", 1.0)


async def test_one_upload_of_all_rows(mock, http):
    route = mock.post(SAVE_URL).mock(return_value=httpx.Response(204))
    given = rows() + rows("seeded")
    await save(http, INGESTION + "/", "dcr-1", given)
    assert route.call_count == 1
    request = route.calls.last.request
    assert json.loads(request.content) == given
    assert request.headers["content-type"] == "application/json"


@pytest.mark.parametrize("status", [200, 202, 302, 400, 403, 413, 429, 500, 503])
async def test_any_other_answer_raises_after_one_try(mock, http, status):
    route = mock.post(SAVE_URL).mock(return_value=httpx.Response(status, text=SECRET))
    with pytest.raises(StoreError) as caught:
        await save(http, INGESTION, "dcr-1", rows())
    assert route.call_count == 1
    assert str(status) in str(caught.value)
    assert SECRET not in "".join(traceback.format_exception(caught.value))


async def test_a_lost_answer_raises_after_one_try(mock, http):
    route = mock.post(SAVE_URL).mock(side_effect=httpx.ReadTimeout(SECRET))
    with pytest.raises(StoreError) as caught:
        await save(http, INGESTION, "dcr-1", rows())
    assert route.call_count == 1
    assert caught.value.__context__ is None and caught.value.__cause__ is None


async def test_a_body_of_one_megabyte_is_not_sent(mock, http):
    route = mock.post(SAVE_URL).mock(return_value=httpx.Response(204))
    big = [{**rows()[1], "GroupName": "x" * MAX_BODY}]
    with pytest.raises(StoreError):
        await save(http, INGESTION, "dcr-1", big)
    assert MAX_BODY <= 1_000_000 and route.call_count == 0


async def test_rows_that_are_not_json_are_not_sent(mock, http):
    route = mock.post(SAVE_URL).mock(return_value=httpx.Response(204))
    for bad in (float("nan"), "\ud800", object()):
        with pytest.raises(StoreError):
            await save(http, INGESTION, "dcr-1", [{**rows()[1], "GroupName": bad}])
    assert route.call_count == 0


def table(*found):
    columns = [{"name": "Topic", "type": "string"}, {"name": "GroupName", "type": "string"}]
    return httpx.Response(200, json={"tables": [{"name": "PrimaryResult", "columns": columns, "rows": list(found)}]})


async def test_the_previous_names_of_one_source_per_topic(mock, http):
    route = mock.post(QUERY_URL).mock(
        return_value=table(["charging", "Home charging"], ["fault", "Red light"], ["charging", "Slow charging"])
    )
    found = await previous_names(http, "ws-1", "seeded")
    assert found == {"charging": ["Home charging", "Slow charging"], "fault": ["Red light"]}
    query = json.loads(route.calls.last.request.content)["query"]
    assert query.count("Source == 'seeded'") == 2 and "'real'" not in query
    assert "max(Run)" in query and "Run == last_run_at" in query
    assert "'other'" in query and "'unnamed group'" in query


async def test_no_names_yet(mock, http):
    mock.post(QUERY_URL).mock(return_value=table())
    assert await previous_names(http, "ws-1", "real") == {}


async def test_an_unknown_source_is_not_asked_for(mock, http):
    route = mock.post(QUERY_URL).mock(return_value=table())
    with pytest.raises(ValueError):
        await previous_names(http, "ws-1", "x' or 1==1")
    assert route.call_count == 0


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(403, text=SECRET),
        httpx.Response(200, text=SECRET),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"tables": []}),
        httpx.Response(200, json={"tables": [{"columns": [{"name": "Topic"}], "rows": []}]}),
        httpx.Response(200, json={**json.loads(table().content), "error": {"code": "PartialError", "message": SECRET}}),
        table(["charging"]),
        table(["charging", None]),
        table(SECRET),
    ],
)
async def test_an_answer_that_does_not_fit_raises(mock, http, answer):
    mock.post(QUERY_URL).mock(return_value=answer)
    with pytest.raises(StoreError) as caught:
        await previous_names(http, "ws-1", "real")
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None


async def test_reading_back_is_tried_again(mock, http):
    route = mock.post(QUERY_URL).mock(side_effect=[httpx.Response(503), table(["charging", "Home charging"])])
    assert await previous_names(http, "ws-1", "real") == {"charging": ["Home charging"]}
    assert route.call_count == 2


def test_the_stream_is_the_one_of_the_table():
    assert store.STREAM == f"Custom-{store.TABLE}"


def test_the_query_names_nothing_the_engine_refuses():
    import re

    query = store._names_query("real")
    assert not re.search(r"\blatest\b", query)
    assert not re.search(r"(?:\blet|\bextend|\bsummarize|,)\s*last\b", query)
