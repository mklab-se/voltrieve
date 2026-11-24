"""GET /admin/insights: what people ask about, read from Log Analytics (no network here)."""

import json
import logging

import httpx
import pytest
import respx

from app.main import create_app
from app.settings import Settings
from app.usage import QUERY_URL, UsageService

WORKSPACE = "ws-1234"
URL = QUERY_URL.format(id=WORKSPACE)
COLS = [
    "Run", "WindowStart", "WindowEnd", "Cut", "Client", "Topic", "CarModel", "RequestKind",
    "Result", "GroupName", "Conversations", "People", "SampleShare",
]
RUN1 = "2026-11-24T02:00:00Z"
WEEK = "2026-11-23T00:00:00Z"
START = "2026-11-16T00:00:00Z"


def row(cut, run=RUN1, end=WEEK, start=START, conversations=10, people=5, share=None, **labels):
    values = {"Run": run, "WindowStart": start, "WindowEnd": end, "Cut": cut, "Client": "", "Topic": "",
              "CarModel": "", "RequestKind": "", "Result": "", "GroupName": "",
              "Conversations": conversations, "People": people, "SampleShare": share}
    values.update({k: v for k, v in labels.items()})
    return [values[c] for c in COLS]


def table(*rows, columns=COLS):
    return {"tables": [{"name": "PrimaryResult", "columns": [{"name": c, "type": "string"} for c in columns],
                        "rows": list(rows)}]}


LATEST = table(
    row("run", conversations=None, people=None, share=0.5),
    row("all", conversations=120),
    row("topic", Topic="charging", conversations=40),
    row("group", Topic="charging", GroupName="Where is the charge port", conversations=15, people=10),
    row("topic", Topic="service", conversations=None),  # no counts: not in rows
)
WEEKS = table(
    row("topic", run="2026-11-17T02:00:00Z", end="2026-11-16T00:00:00Z", Topic="charging", conversations=30),
    row("topic", Topic="charging", conversations=40),
)


class Time:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Tokens:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return "la-token"


@pytest.fixture
def time_():
    return Time()


@pytest.fixture
def tokens():
    return Tokens()


@pytest.fixture
def usage_app(validator, backend, time_, tokens):
    def make(workspace=WORKSPACE):
        from app.attachments.store import AttachmentStore
        from app.backends import BackendRegistry
        from app.turns import TurnStore

        settings = Settings(log_analytics_workspace_id=workspace)
        registry = BackendRegistry(settings, {"loop": lambda s: backend})
        service = UsageService(workspace, tokens, clock=time_)
        return create_app(settings, validator, registry, TurnStore(), attachments=AttachmentStore(),
                          usage=service)

    return make


@pytest.fixture
async def http(usage_app):
    transport = httpx.ASGITransport(app=usage_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def admin(make_token):
    return {"Authorization": f"Bearer {make_token(roles=['Chat.User', 'Dashboard.View'])}"}


def answers(route_rows=LATEST, route_weeks=WEEKS):
    """A mocked Log Analytics: the weeks query holds a join, the rows query does not."""

    def respond(request):
        query = json.loads(request.content)["query"]
        return httpx.Response(200, json=route_weeks if "join" in query else route_rows)

    return respond


@respx.mock
async def test_no_token_gives_401(http):
    route = respx.post(URL).mock(side_effect=answers())
    assert (await http.get("/admin/insights")).status_code == 401
    assert not route.called


@respx.mock
async def test_no_dashboard_role_gives_403_and_nothing_is_queried(http, auth):
    route = respx.post(URL).mock(side_effect=answers())
    assert (await http.get("/admin/insights", headers=auth)).status_code == 403
    assert not route.called


@respx.mock
async def test_a_source_outside_real_and_seeded_gives_422(http, admin):
    route = respx.post(URL).mock(side_effect=answers())
    assert (await http.get("/admin/insights", params={"source": "other"}, headers=admin)).status_code == 422
    assert not route.called


@respx.mock
async def test_role_is_checked_before_the_source(http, auth):
    assert (await http.get("/admin/insights", params={"source": "other"}, headers=auth)).status_code == 403


async def test_not_configured_gives_503_without_querying(usage_app, admin):
    transport = httpx.ASGITransport(app=usage_app(workspace=""))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        with respx.mock:
            route = respx.post(url__startswith="https://api.loganalytics.azure.com").mock()
            r = await c.get("/admin/insights", headers=admin)
            assert r.status_code == 503 and not route.called


@respx.mock
async def test_the_answer_has_the_run_the_window_and_the_rows(http, admin, tokens):
    route = respx.post(URL).mock(side_effect=answers())
    r = await http.get("/admin/insights", headers=admin)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["source"] == "real" and body["run"] == RUN1
    assert body["window"] == {"start": START, "end": WEEK} and body["sample_share"] == 0.5
    assert [x["cut"] for x in body["rows"]] == ["all", "topic", "group"]  # no run row, no row without counts
    assert body["rows"][2] == {"cut": "group", "client": "", "topic": "charging", "car_model": "",
                               "request_kind": "", "result": "", "group": "Where is the charge port",
                               "conversations": 15, "people": 10}
    assert route.calls[0].request.headers["authorization"] == "Bearer la-token"
    assert tokens.calls == 1


@respx.mock
async def test_the_weeks_are_topic_points_in_order(http, admin):
    respx.post(URL).mock(side_effect=answers())
    body = (await http.get("/admin/insights", headers=admin)).json()
    assert body["weeks"] == [
        {"end": "2026-11-16T00:00:00Z", "topic": "charging", "conversations": 30, "people": 5},
        {"end": WEEK, "topic": "charging", "conversations": 40, "people": 5},
    ]


@respx.mock
async def test_the_queries_take_the_latest_run_of_each_window_end_and_are_limited(http, admin):
    route = respx.post(URL).mock(side_effect=answers())
    await http.get("/admin/insights", headers=admin)
    queries = [json.loads(c.request.content)["query"] for c in route.calls]
    rows_q = next(q for q in queries if "join" not in q)
    weeks_q = next(q for q in queries if "join" in q)
    assert "Run == latest" in rows_q
    assert "summarize Run = max(Run) by WindowEnd" in weeks_q and "top " not in weeks_q
    assert "ago(84d)" in weeks_q and "max(Run))" in rows_q and "arg_max" not in rows_q
    for q in queries:
        assert "take 2001" in q and "ago(400d)" in q and 'Source == "real"' in q


@respx.mock
async def test_the_source_in_the_query_is_ours_not_the_clients(http, admin):
    route = respx.post(URL).mock(side_effect=answers())
    evil = 'real" or Source != "x'
    assert (await http.get("/admin/insights", params={"source": evil}, headers=admin)).status_code == 422
    assert not route.called
    await http.get("/admin/insights", params={"source": "seeded"}, headers=admin)
    for c in route.calls:
        query = json.loads(c.request.content)["query"]
        assert 'Source == "seeded"' in query and "real" not in query


@respx.mock
async def test_no_run_gives_no_window_and_empty_lists(http, admin):
    respx.post(URL).mock(side_effect=answers(table(), table()))
    body = (await http.get("/admin/insights", headers=admin)).json()
    assert body == {"source": "real", "run": None, "window": None, "sample_share": None,
                    "rows": [], "weeks": []}


@respx.mock
async def test_a_failed_query_gives_503_with_a_fixed_text(http, admin, caplog):
    respx.post(URL).mock(return_value=httpx.Response(500, text="SECRET-DETAIL workspace ws-1234"))
    with caplog.at_level(logging.DEBUG):
        r = await http.get("/admin/insights", headers=admin)
    assert r.status_code == 503 and "SECRET-DETAIL" not in r.text and "ws-1234" not in r.text
    assert "SECRET-DETAIL" not in caplog.text


@respx.mock
async def test_a_network_error_gives_503(http, admin):
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    assert (await http.get("/admin/insights", headers=admin)).status_code == 503


@respx.mock
@pytest.mark.parametrize("bad", [{"tables": []}, {"error": "x"}, [], {"tables": [{"columns": [], "rows": []}]},
                                 table(row("all"), columns=["Run", "Cut"]),
                                 {"tables": [{"columns": [{"name": c} for c in COLS], "rows": "no"}]}])
async def test_a_wrong_answer_shape_gives_503_with_a_fixed_text(http, admin, bad):
    respx.post(URL).mock(return_value=httpx.Response(200, json=bad))
    r = await http.get("/admin/insights", headers=admin)
    assert r.status_code == 503 and r.json() == {"detail": "The usage insights are not available right now"}


@respx.mock
async def test_group_names_are_json_strings_and_never_logged(http, admin, caplog):
    name = '<script>alert(1)</script> "quoted"'
    respx.post(URL).mock(side_effect=answers(table(row("run", conversations=None), row("group", GroupName=name, conversations=5)), WEEKS))
    with caplog.at_level(logging.DEBUG):
        r = await http.get("/admin/insights", headers=admin)
    assert r.json()["rows"][0]["group"] == name and r.headers["content-type"].startswith("application/json")
    assert "script" not in caplog.text


@respx.mock
async def test_the_weeks_keep_one_run_per_window_end(http, admin):
    """The query joins on the latest run of each window end, so an older run never shows."""
    route = respx.post(URL).mock(side_effect=answers())
    await http.get("/admin/insights", headers=admin)
    weeks_q = next(json.loads(c.request.content)["query"] for c in route.calls
                   if "join" in json.loads(c.request.content)["query"])
    assert "join kind=inner ends on WindowEnd, Run" in weeks_q and 'Cut == "topic"' in weeks_q


@respx.mock
async def test_the_answer_is_cached_for_five_minutes_per_source(http, admin, time_):
    route = respx.post(URL).mock(side_effect=answers())
    await http.get("/admin/insights", headers=admin)
    calls = route.call_count
    time_.now += 299
    await http.get("/admin/insights", headers=admin)
    assert route.call_count == calls
    await http.get("/admin/insights", params={"source": "seeded"}, headers=admin)
    assert route.call_count == calls * 2  # another source has its own cache
    time_.now += 2
    await http.get("/admin/insights", headers=admin)
    assert route.call_count == calls * 3


@respx.mock
async def test_the_role_is_checked_before_the_cache(http, admin, auth):
    respx.post(URL).mock(side_effect=answers())
    assert (await http.get("/admin/insights", headers=admin)).status_code == 200
    assert (await http.get("/admin/insights", headers=auth)).status_code == 403


@respx.mock
async def test_a_failure_is_not_cached(http, admin):
    route = respx.post(URL).mock(return_value=httpx.Response(500))
    assert (await http.get("/admin/insights", headers=admin)).status_code == 503
    route.mock(side_effect=answers())
    assert (await http.get("/admin/insights", headers=admin)).status_code == 200


@respx.mock
async def test_two_requests_at_once_make_one_query(http, admin):
    import asyncio

    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return answers()(request)

    route = respx.post(URL).mock(side_effect=slow)
    first = asyncio.create_task(http.get("/admin/insights", headers=admin))
    second = asyncio.create_task(http.get("/admin/insights", headers=admin))
    for _ in range(20):
        await asyncio.sleep(0)
    gate.set()
    a, b = await first, await second
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert route.call_count == 2  # the rows and the weeks, once


@respx.mock
@pytest.mark.parametrize("which", ["rows", "weeks"])
@pytest.mark.parametrize("error", [{"code": "PartialError"}, "x", None, 0])
async def test_a_partial_failure_body_is_not_an_answer_and_is_not_cached(http, admin, which, error):
    bad = {**(LATEST if which == "rows" else WEEKS), "error": error}
    route = respx.post(URL).mock(side_effect=answers(bad if which == "rows" else LATEST,
                                                      bad if which == "weeks" else WEEKS))
    r = await http.get("/admin/insights", headers=admin)
    assert r.status_code == 503 and r.json() == {"detail": "The usage insights are not available right now"}
    route.mock(side_effect=answers())
    assert (await http.get("/admin/insights", headers=admin)).status_code == 200


@respx.mock
async def test_the_weeks_hold_every_night_not_twelve(http, admin):
    nights = table(*[row("topic", end=f"2026-11-{d:02d}T00:00:00Z", Topic="charging") for d in range(1, 21)])
    respx.post(URL).mock(side_effect=answers(LATEST, nights))
    assert len((await http.get("/admin/insights", headers=admin)).json()["weeks"]) == 20


@respx.mock
async def test_hitting_the_row_limit_gives_503(http, admin):
    many = table(row("run", conversations=None), *[row("all") for _ in range(2000)])
    respx.post(URL).mock(side_effect=answers(many, WEEKS))
    assert (await http.get("/admin/insights", headers=admin)).status_code == 503


@respx.mock
async def test_rows_without_a_run_row_give_503(http, admin):
    respx.post(URL).mock(side_effect=answers(table(row("all")), WEEKS))
    assert (await http.get("/admin/insights", headers=admin)).status_code == 503


@respx.mock
async def test_a_surprise_in_the_rows_is_a_503_not_a_500(http, admin):
    odd = table(row("run", conversations=None, share="x"), row("all", Run=None, Cut=["x"]))
    respx.post(URL).mock(side_effect=answers(odd, WEEKS))
    r = await http.get("/admin/insights", headers=admin)
    assert r.status_code != 500


@respx.mock
async def test_non_finite_counts_are_not_rows(http, admin):
    odd = table(row("run", conversations=None), row("all", conversations=float("nan")),
                row("topic", Topic="a", conversations=7))
    respx.post(URL).mock(side_effect=lambda req: httpx.Response(
        200, content=json.dumps(odd if "join" not in json.loads(req.content)["query"] else WEEKS),
        headers={"content-type": "application/json"}))
    rows = (await http.get("/admin/insights", headers=admin)).json()["rows"]
    assert [r["cut"] for r in rows] == ["topic"]


async def test_a_slow_query_times_out_and_frees_the_lock(usage_app, admin, monkeypatch):
    import asyncio

    import app.usage as usage

    monkeypatch.setattr(usage, "FETCH_SECONDS", 0.05)
    transport = httpx.ASGITransport(app=usage_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        with respx.mock:
            async def hang(request):
                await asyncio.sleep(5)

            route = respx.post(URL).mock(side_effect=hang)
            r = await c.get("/admin/insights", headers=admin)
            assert r.status_code == 503 and r.json()["detail"] == "The usage insights are not available right now"
            route.mock(side_effect=answers())
            assert (await c.get("/admin/insights", headers=admin)).status_code == 200
