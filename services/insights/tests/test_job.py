import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx

from voltrieve_insights import groups, job

P = "https://project.test/api/projects/p"
MODELS = "https://models.test"
LANGUAGE = "https://language.test"
INGESTION = "https://rule.test"
NOW = datetime(2026, 11, 24, 2, 30, tzinfo=UTC)  # the window is 16 to 22 November
DAY = datetime(2026, 11, 18, 12, tzinfo=UTC)
ENV = {
    "FOUNDRY_PROJECT_ENDPOINT": P,
    "FOUNDRY_OPENAI_ENDPOINT": MODELS,
    "LANGUAGE_ENDPOINT": LANGUAGE,
    "INSIGHTS_INGESTION_ENDPOINT": INGESTION,
    "INSIGHTS_RULE_ID": "dcr-1",
    "LOG_ANALYTICS_WORKSPACE_ID": "ws-1",
    "AZURE_CLIENT_ID": "client-1",
    "INSIGHTS_NOTICE_DATE": "2026-11-01",
    "INSIGHTS_LIMIT": "5",
    "INSIGHTS_CAP": "1000",
    "INSIGHTS_SEEDED_CAP": "500",
    "MODEL_DEPLOYMENT": "gpt-6-luna",
}
SECRET = "Plantedsecretword"
GOOD = {"topic": "charging", "car_model": None, "kind": "how_to", "result": "answered", "intent": "Wants to charge at home"}


def said(text, role="user"):
    part = "input_text" if role == "user" else "output_text"
    return {"type": "message", "role": role, "content": [{"type": part, "text": text}]}


class World:
    """Every service of a run, in memory."""

    def __init__(self, router):
        self.router = router
        self.conversations = []
        self.items = {}
        self.read = []  # ids whose text was read
        self.names = {"real": [], "seeded": []}
        self.asked_names = []
        self.names_queries = []
        self.group_data = []
        self.uploads = []
        self.labels = lambda data: GOOD
        self.group_name = "Home charging"
        self.bad_names = ()
        self.tokens = []
        router.get(f"{P}/openai/v1/conversations").mock(side_effect=self._list)
        router.get(url__regex=rf"{P}/openai/v1/conversations/[^/]+/items").mock(side_effect=self._items)
        router.post(f"{MODELS}/openai/v1/chat/completions").mock(side_effect=self._model)
        router.post(url__startswith=f"{LANGUAGE}/language/:analyze-text").mock(side_effect=self._language)
        router.post("https://api.loganalytics.azure.com/v1/workspaces/ws-1/query").mock(side_effect=self._names)
        router.post(url__startswith=f"{INGESTION}/dataCollectionRules/dcr-1/streams/").mock(side_effect=self._save)
        self.saved_status = 204
        self.broken = {}

    def add(self, count, seeded=False, said_by="o", text="How do I charge at home?", started=DAY, **meta):
        for n in range(count):
            ident = f"conv_{len(self.conversations)}"
            metadata = {"client": "car", "backend": "loop", "owner_oid": f"{said_by}{n}", **meta}
            created = started
            if seeded:
                metadata |= {"seeded": "true", "started": started.date().isoformat()}
                created = NOW
            self.conversations.append({"id": ident, "created_at": int(created.timestamp()), "metadata": metadata})
            self.items[ident] = [said(text), said("Plug it in.", "assistant")]

    def _broken(self, name):
        answer = self.broken.get(name)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _list(self, request):
        self.tokens.append(request.headers["authorization"])
        newest_first = sorted(self.conversations, key=lambda c: -c["created_at"])
        return self._broken("list") or httpx.Response(200, json={"data": newest_first, "has_more": False})

    def _items(self, request):
        ident = request.url.path.split("/")[-2]
        self.read.append(ident)
        return self._broken("read") or httpx.Response(200, json={"data": self.items[ident], "has_more": False})

    def _model(self, request):
        body = json.loads(request.content)
        data = body["messages"][1]["content"]
        if "groups" in body["response_format"]["json_schema"]["schema"]["properties"]:
            self.group_data.append(json.loads(data))
            if answer := self._broken("group"):
                return answer
            members = [i["n"] for i in json.loads(data)["intents"]]
            found = {"groups": [{"name": self.group_name, "members": members}]}
        else:
            if answer := self._broken("label"):
                return answer
            found = self.labels(data)
            if isinstance(found, httpx.Response):
                return found
        choice = {"finish_reason": "stop", "message": {"content": json.dumps(found)}}
        return httpx.Response(200, json={"choices": [choice], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    def _language(self, request):
        if answer := self._broken("detection"):
            return answer
        documents = json.loads(request.content)["analysisInput"]["documents"]
        found = [
            {"id": d["id"], "entities": [{"category": "Person"}] if d["text"] in self.bad_names else []} for d in documents
        ]
        return httpx.Response(200, json={"results": {"documents": found, "errors": []}})

    def _names(self, request):
        if answer := self._broken("previous names"):
            return answer
        query = json.loads(request.content)["query"]
        (source,) = [s for s in self.names if f"Source == '{s}'" in query]
        self.asked_names.append(source)
        self.names_queries.append(query)
        columns = [{"name": "Topic"}, {"name": "GroupName"}]
        return httpx.Response(200, json={"tables": [{"columns": columns, "rows": self.names[source]}]})

    def _save(self, request):
        self.uploads.append(request)
        return self._broken("save") or httpx.Response(204)

    @property
    def rows(self):
        return [row for request in self.uploads for row in json.loads(request.content)]

    def cuts(self, source):
        return [row["Cut"] for row in self.rows if row["Source"] == source]


@pytest.fixture(autouse=True)
def process(monkeypatch):
    """What main changes in the process is put back after each test."""
    for name in ("excepthook", "unraisablehook"):
        monkeypatch.setattr(sys, name, getattr(sys, name))
    monkeypatch.setattr(job.threading, "excepthook", job.threading.excepthook)
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in [h for h in root.handlers if h not in before]:
        root.removeHandler(handler)


@pytest.fixture
def world(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(job, "_now", lambda: NOW)
    token = SimpleNamespace(token=f"token-{SECRET}", expires_on=2**40, refresh_on=None, token_type="Bearer")
    credential = SimpleNamespace(get_token=lambda *scopes, **kw: token)
    monkeypatch.setattr(job, "_credential", lambda client_id: credential)
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield World(router)


def test_one_upload_holds_both_sources(world, capsys):
    world.add(6)
    world.add(7, seeded=True)
    assert job.main([]) == 0
    assert len(world.uploads) == 1 and len(world.uploads[0].content) < 1_000_000
    for source in ("real", "seeded"):
        assert world.cuts(source)[0] == "run" and world.cuts(source).count("run") == 1
        assert {"all", "client", "topic", "group"} <= set(world.cuts(source))
    for row in world.rows:
        assert row["WindowStart"] == "2026-11-16T00:00:00Z" and row["WindowEnd"] == "2026-11-22T00:00:00Z"
        assert row["Run"] == row["TimeGenerated"] == "2026-11-24T02:30:00Z"
        assert row["SampleShare"] == 1.0
    groups_saved = [r["GroupName"] for r in world.rows if r["Cut"] == "group"]
    assert groups_saved == ["Home charging", "Home charging"]


def test_a_night_with_nobody_still_saves_one_run_row_per_source(world):
    assert job.main([]) == 0
    assert [(r["Source"], r["Cut"]) for r in world.rows] == [("real", "run"), ("seeded", "run")]
    assert all(r["WindowStart"] and r["WindowEnd"] for r in world.rows)
    assert all(r["Conversations"] is None and r["People"] is None for r in world.rows)


def test_four_made_up_people_and_one_real_give_no_row_in_either_source(world):
    world.add(4, seeded=True, said_by="made-up-")
    world.add(1)
    assert job.main([]) == 0
    assert world.cuts("real") == ["run"] and world.cuts("seeded") == ["run"]


def test_four_are_not_saved_and_five_are(world):
    world.add(4)
    world.add(5, seeded=True)
    assert job.main([]) == 0
    assert world.cuts("real") == ["run"]
    assert "topic" in world.cuts("seeded")


SERVER_DOWN = httpx.Response(500, text=SECRET)
REFUSED_KEY = httpx.Response(403, json={"error": {"message": SECRET}})
BROKEN = [
    ("list", SERVER_DOWN, "list", "FetchError"),
    ("list", httpx.ConnectError(SECRET), "list", "FetchError"),
    ("read", REFUSED_KEY, "read", "FetchError"),
    ("label", SERVER_DOWN, "label", "ModelError"),
    ("label", httpx.Response(429, text=SECRET), "label", "ModelError"),
    ("previous names", REFUSED_KEY, "previous names", "StoreError"),
    ("group", SERVER_DOWN, "group", "ModelError"),
    ("detection", SERVER_DOWN, "group", "PersonalDataError"),
    ("detection", httpx.ReadTimeout(SECRET), "group", "PersonalDataError"),
    ("save", httpx.Response(200, text=SECRET), "save", "StoreError"),
    ("save", SERVER_DOWN, "save", "StoreError"),
]


@pytest.mark.parametrize("where, answer, step, error", BROKEN)
def test_a_failure_in_any_step_saves_nothing(world, capfd, caplog, where, answer, step, error):
    caplog.set_level(logging.DEBUG)
    world.add(6)
    world.add(6, seeded=True)
    world.broken[where] = answer
    assert job.main([]) == 1
    assert len(world.uploads) == (1 if where == "save" else 0)
    out, err = capfd.readouterr()
    assert out.splitlines()[-1] == f"failed: {step}: {error}"
    assert err == "" and SECRET not in out + caplog.text
    assert sum(line.startswith("failed") for line in out.splitlines()) == 1


@pytest.mark.parametrize(
    "name, step", [("select", "select"), ("sample", "sample"), ("totals", "totals"), ("table_rows", "totals")]
)
def test_a_failure_in_our_own_code_saves_nothing(world, monkeypatch, capfd, name, step):
    def fail(*args, **kwargs):
        try:
            raise ValueError(SECRET)
        except ValueError as cause:
            raise KeyError(SECRET) from cause

    world.add(6)
    monkeypatch.setattr(job, name, fail)
    assert job.main([]) == 1
    out, err = capfd.readouterr()
    assert world.uploads == [] and out.splitlines()[-1] == f"failed: {step}: KeyError"
    assert SECRET not in out + err and "Traceback" not in out + err


def test_an_unset_notice_date_reads_no_real_conversation(world, monkeypatch):
    monkeypatch.setenv("INSIGHTS_NOTICE_DATE", "")
    world.add(6)
    world.add(5, seeded=True)
    assert job.main([]) == 0
    assert world.read == [f"conv_{n}" for n in range(6, 11)]
    assert world.cuts("real") == ["run"] and "topic" in world.cuts("seeded")


def test_before_the_notice_nothing_real_is_read(world, monkeypatch):
    monkeypatch.setenv("INSIGHTS_NOTICE_DATE", "2026-11-19")
    world.add(6)
    assert job.main([]) == 0
    assert world.read == []


def test_a_window_end_reads_made_up_conversations_only_and_writes_no_real_row(world):
    world.add(6, started=datetime(2026, 11, 3, tzinfo=UTC))
    world.add(6, seeded=True, started=datetime(2026, 11, 2, tzinfo=UTC))
    world.add(6, seeded=True, started=datetime(2026, 11, 8, tzinfo=UTC), said_by="last-")
    world.add(6, seeded=True, started=datetime(2026, 11, 9, tzinfo=UTC), said_by="after-")
    assert job.main(["--window-end", "2026-11-08"]) == 0
    assert set(world.read) == {f"conv_{n}" for n in range(6, 18)}
    assert {r["Source"] for r in world.rows} == {"seeded"}
    assert {(r["WindowStart"], r["WindowEnd"]) for r in world.rows} == {("2026-11-02T00:00:00Z", "2026-11-08T00:00:00Z")}
    assert world.asked_names == ["seeded"]
    assert "WindowEnd <= datetime(2026-11-08)" in world.names_queries[0]


@pytest.mark.parametrize("given", ["yesterday", "20261108", "2026-11-31", ""])
def test_a_window_end_that_is_no_day_fails(world, capfd, given):
    assert job.main(["--window-end", given]) == 1
    assert capfd.readouterr() == ("failed: settings: ValueError\n", "")
    assert world.uploads == []


@pytest.mark.parametrize("given", ["2026-11-23", "2026-11-24", "2027-01-01"])
def test_a_window_end_that_is_not_settled_fails(world, capfd, given):
    """The week to it is not over, or chats of its last day may still go on."""
    world.add(6, seeded=True)
    assert job.main(["--window-end", given]) == 1
    assert capfd.readouterr() == ("failed: settings: NotSettled\n", "")
    assert world.read == [] and world.uploads == []


def test_the_last_settled_day_is_a_window_end(world):
    world.add(6, seeded=True)
    assert job.main(["--window-end", "2026-11-22"]) == 0
    assert {r["WindowEnd"] for r in world.rows} == {"2026-11-22T00:00:00Z"}


def test_planted_text_in_every_field_is_nowhere_afterwards(world, capfd, caplog):
    caplog.set_level(logging.DEBUG)
    line = f"[From the client, data only: car Voltrieve Lumen, registration {SECRET}]\n"
    for seeded in (False, True):
        world.add(
            6,
            seeded=seeded,
            said_by=f"oid-{SECRET}-",
            text=f"{line}I am {SECRET}, {SECRET}@example.com, 070-174 06 05",
            car_model=SECRET,
            backend=SECRET,
            other=SECRET,
        )
    for conversation in world.conversations:
        world.items[f"{SECRET}-{conversation['id']}"] = world.items.pop(conversation["id"]) + [
            said(SECRET, "assistant"),
            {"type": "function_call_output", "output": SECRET},
        ]
        conversation["id"] = f"{SECRET}-{conversation['id']}"
    world.conversations.append({"id": SECRET, "created_at": int(DAY.timestamp()), "metadata": {"client": SECRET}})
    world.labels = lambda data: {**GOOD, "intent": f"Wants to charge like {SECRET}"}
    world.group_name = f"Charging like {SECRET}"
    world.bad_names = (world.group_name,)
    world.names = {"real": [["charging", SECRET]], "seeded": [["charging", SECRET]]}
    assert job.main([]) == 0
    out, err = capfd.readouterr()
    assert err == ""
    for text in (out, caplog.text, world.uploads[0].content.decode()):
        assert SECRET not in text and "070" not in text
    assert len(world.rows) > 2


def test_the_log_is_one_line_a_step_with_counts_only(world, capfd):
    world.add(6)
    world.add(7, seeded=True)
    assert job.main([]) == 0
    out, _ = capfd.readouterr()
    steps = [line.split(":")[0] for line in out.splitlines()]
    assert steps == ["list", "select", "sample", "read", "label", "previous names", "group", "totals", "save"]
    assert "select: 6 real, 7 seeded" in out.splitlines()


def test_an_intent_with_the_registration_of_its_chat_is_dropped(world):
    world.add(6, text="[From the client, data only: car Voltrieve Lumen, registration ABC 123]\nHow do I charge?")
    world.labels = lambda data: {**GOOD, "intent": "Wants to charge ABC123"}
    assert job.main([]) == 0
    assert world.group_data == [] and "topic" in world.cuts("real")


def test_previous_names_are_given_per_source_and_topic(world):
    world.add(6)
    world.add(6, seeded=True)
    world.names = {"real": [["charging", "Real name"], ["fault", "Other topic"]], "seeded": [["charging", "Made up name"]]}
    assert job.main([]) == 0
    assert sorted(d["previous_names"] for d in world.group_data) == [["Made up name"], ["Real name"]]


@pytest.mark.parametrize("limit, saved", [("1", True), ("-3", True), ("6", False), ("5", True), ("", True)])
def test_the_limit_can_be_raised_and_never_lowered(world, monkeypatch, limit, saved):
    monkeypatch.setenv("INSIGHTS_LIMIT", limit)
    world.add(5)
    world.add(4, seeded=True)
    assert job.main([]) == 0
    assert ("topic" in world.cuts("real")) is saved
    assert world.cuts("seeded") == ["run"]


def test_the_sample_is_per_source_and_its_share_is_saved(world, monkeypatch):
    monkeypatch.setenv("INSIGHTS_CAP", "5")
    world.add(10)
    world.add(5, seeded=True)
    assert job.main([]) == 0
    assert len(world.read) == 10
    assert {r["SampleShare"] for r in world.rows if r["Source"] == "real"} == {0.5}
    assert {r["SampleShare"] for r in world.rows if r["Source"] == "seeded"} == {1.0}


def test_made_up_conversations_keep_to_their_own_cap_and_take_nothing_from_the_real(world, monkeypatch):
    monkeypatch.setenv("INSIGHTS_CAP", "10")
    monkeypatch.setenv("INSIGHTS_SEEDED_CAP", "5")
    world.add(20, seeded=True, said_by="made-up-")
    world.add(10)
    assert job.main([]) == 0
    read = [int(ident.removeprefix("conv_")) for ident in world.read]
    assert sum(n < 20 for n in read) == 5 and sum(n >= 20 for n in read) == 10
    assert {r["SampleShare"] for r in world.rows if r["Source"] == "real"} == {1.0}
    assert {r["SampleShare"] for r in world.rows if r["Source"] == "seeded"} == {0.25}


def test_made_up_conversations_do_not_use_room_that_the_real_leave(world, monkeypatch):
    monkeypatch.setenv("INSIGHTS_CAP", "30")
    monkeypatch.setenv("INSIGHTS_SEEDED_CAP", "5")
    world.add(20, seeded=True, said_by="made-up-")
    assert job.main([]) == 0
    assert len(world.read) == 5
    world.read.clear()
    assert job.main(["--window-end", "2026-11-22"]) == 0
    assert len(world.read) == 5


def test_a_conversation_with_no_user_text_is_dropped_before_the_model(world):
    world.add(5)
    world.items["conv_0"] = [said("Hello, I am Runa.", "assistant")]
    asked = []
    world.labels = lambda data: asked.append(data) or GOOD
    assert job.main([]) == 0
    assert len(asked) == 4 and world.cuts("real") == ["run"]


def refuse_first(count):
    left = [count]

    def labels(data):
        left[0] -= 1
        if left[0] >= 0:
            return httpx.Response(400, json={"error": {"code": "content_filter", "message": SECRET}})
        return GOOD

    return labels


def test_up_to_a_tenth_refused_is_skipped(world, capfd):
    world.add(20)
    world.labels = refuse_first(2)
    assert job.main([]) == 0
    assert [r["Conversations"] for r in world.rows if r["Cut"] == "all"] == [20]  # 18, rounded
    out, err = capfd.readouterr()
    assert "label: 18 labelled, under 5 refused" in out.splitlines() and SECRET not in out + err


def test_more_than_a_tenth_refused_fails_the_run(world, capfd):
    world.add(20)
    world.labels = refuse_first(3)
    assert job.main([]) == 1
    assert world.uploads == []
    assert capfd.readouterr().out.splitlines()[-1] == "failed: label: TooManyRefused"


def refuse_asking(word):
    def labels(data):
        if word in data:
            return httpx.Response(400, json={"error": {"code": "content_filter"}})
        return GOOD

    return labels


@pytest.mark.parametrize("refused_seeded", [False, True])
def test_a_refused_source_fails_the_run_however_many_the_other_has(world, capfd, refused_seeded):
    world.add(8, seeded=refused_seeded, text="Please refuse this")
    world.add(80, seeded=not refused_seeded, said_by="many-")
    world.labels = refuse_asking("refuse this")
    assert job.main([]) == 1
    assert world.uploads == []
    assert capfd.readouterr().out.splitlines()[-1] == "failed: label: TooManyRefused"


def test_a_tenth_of_each_source_refused_is_skipped(world):
    for seeded, said_by in ((False, "a-"), (True, "b-")):
        world.add(2, seeded=seeded, said_by=said_by, text="Please refuse this")
        world.add(18, seeded=seeded, said_by=said_by)
    world.labels = refuse_asking("refuse this")
    assert job.main([]) == 0


@pytest.mark.parametrize("count, shown", [(0, "0"), (1, "under 5"), (4, "under 5"), (5, "5"), (12, "12")])
def test_a_small_count_is_never_logged(world, capfd, count, shown):
    world.add(count)
    assert job.main([]) == 0
    lines = capfd.readouterr().out.splitlines()
    for line in (
        f"list: {shown}",
        f"select: {shown} real, 0 seeded",
        f"sample: {shown} real, 0 seeded",
        f"read: {shown} with user text",
        f"label: {shown} labelled, 0 refused",
        f"group: {shown} in a group, 0 calls refused",
    ):
        assert line in lines


def test_previous_names_are_not_counted_when_few(world, capfd):
    world.names["real"] = [["charging", "Home charging"]]
    assert job.main([]) == 0
    assert "previous names: under 5 real, 0 seeded" in capfd.readouterr().out.splitlines()


def test_a_refused_group_is_counted_and_does_not_fail_the_run(world, capfd):
    world.add(6)
    world.broken["group"] = httpx.Response(400, json={"error": {"code": "content_filter"}})
    assert job.main([]) == 0
    assert "group" not in world.cuts("real") and "topic" in world.cuts("real")
    (line,) = [line for line in capfd.readouterr().out.splitlines() if line.startswith("group:")]
    assert line == "group: 0 in a group, under 5 calls refused"


def test_texts_are_read_and_labelled_eight_at_a_time(world):
    world.add(30)
    now, most = {"read": 0, "label": 0}, {"read": 0, "label": 0}

    def slow(name, answer):
        async def handler(request):
            now[name] += 1
            most[name] = max(most[name], now[name])
            await asyncio.sleep(0.005)
            now[name] -= 1
            return answer(request)

        return handler

    world.router.get(url__regex=rf"{P}/openai/v1/conversations/[^/]+/items").mock(side_effect=slow("read", world._items))
    world.router.post(f"{MODELS}/openai/v1/chat/completions").mock(side_effect=slow("label", world._model))
    assert job.main([]) == 0
    assert most == {"read": 8, "label": 8}


def test_the_run_ends_itself_at_the_deadline(world, monkeypatch, capfd):
    assert job.DEADLINE == 25 * 60
    monkeypatch.setattr(job, "DEADLINE", 0.05)

    async def never(request):
        await asyncio.sleep(60)

    world.add(6)
    world.router.get(url__regex=rf"{P}/openai/v1/conversations/[^/]+/items").mock(side_effect=never)
    assert job.main([]) == 1
    assert world.uploads == []
    out, err = capfd.readouterr()
    assert out.splitlines()[-1] == "failed: read: TimeoutError" and err == ""


def test_a_slow_close_after_the_upload_is_no_failure(world, monkeypatch, capfd):
    monkeypatch.setattr(job, "DEADLINE", 0.3)
    closed = httpx.AsyncClient.__aexit__

    async def slow(self, *args):
        if world.uploads:
            await asyncio.sleep(0.4)
        await closed(self, *args)

    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", slow)
    assert job.main([]) == 0
    out, err = capfd.readouterr()
    assert len(world.uploads) == 1 and out.splitlines()[-1].startswith("save: ") and err == ""


@pytest.mark.parametrize("error", [OSError, asyncio.CancelledError])
def test_a_close_that_fails_after_the_upload_is_no_failure(world, monkeypatch, capfd, error):
    async def fails(self, *args):
        raise error(SECRET)

    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", fails)
    assert job.main([]) == 0
    out, err = capfd.readouterr()
    assert len(world.uploads) == 1 and "failed" not in out and SECRET not in out + err


def test_a_slow_upload_is_still_ended_by_the_deadline(world, monkeypatch, capfd):
    monkeypatch.setattr(job, "DEADLINE", 0.3)

    async def never(request):
        await asyncio.sleep(60)

    world.router.post(url__startswith=f"{INGESTION}/dataCollectionRules/dcr-1/streams/").mock(side_effect=never)
    assert job.main([]) == 1
    assert capfd.readouterr().out.splitlines()[-1] == "failed: save: TimeoutError"


def test_a_token_that_cannot_be_had_fails_without_a_word_of_why(world, monkeypatch, capfd):
    def no_token(*scopes, **kw):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(job, "_credential", lambda client_id: SimpleNamespace(get_token=no_token))
    assert job.main([]) == 1
    out, err = capfd.readouterr()
    assert out == "failed: list: FetchError\n" and err == ""


async def test_every_request_asks_for_a_token(world):
    given = iter(("first", "second"))
    async with httpx.AsyncClient(base_url=P, auth=job.Bearer(lambda: next(given))) as http:
        await http.get("/openai/v1/conversations")
        await http.get("/openai/v1/conversations")
    assert world.tokens == ["Bearer first", "Bearer second"]


def test_each_service_gets_a_token_of_its_own_scope(world, monkeypatch):
    asked = []

    def get_token(*scopes, **kw):
        asked.extend(scopes)
        return SimpleNamespace(token="t", expires_on=2**40, refresh_on=None, token_type="Bearer")

    monkeypatch.setattr(job, "_credential", lambda client_id: SimpleNamespace(get_token=get_token))
    world.add(6)
    assert job.main([]) == 0
    assert set(asked) == {
        "https://ai.azure.com/.default",
        "https://cognitiveservices.azure.com/.default",
        "https://api.loganalytics.io/.default",
        "https://monitor.azure.com/.default",
    }


def test_the_job_uses_its_own_identity_and_a_developer_their_own(monkeypatch):
    monkeypatch.setattr(job, "ManagedIdentityCredential", lambda client_id: ("managed", client_id))
    monkeypatch.setattr(job, "DefaultAzureCredential", lambda: "default")
    assert job._credential("client-1") == ("managed", "client-1")
    assert job._credential("") == "default"


@pytest.mark.parametrize(
    "name, value",
    [(n, "") for n in list(ENV)[:6]]
    + [
        ("LANGUAGE_ENDPOINT", "http://language.test"),
        ("INSIGHTS_NOTICE_DATE", "soon"),
        ("INSIGHTS_NOTICE_DATE", "20261101"),
        ("INSIGHTS_LIMIT", "five"),
        ("INSIGHTS_CAP", "0"),
        ("INSIGHTS_CAP", "-1"),
        ("INSIGHTS_SEEDED_CAP", "0"),
        ("INSIGHTS_SEEDED_CAP", "-1"),
        ("INSIGHTS_SEEDED_CAP", "many"),
    ],
)
def test_settings_that_do_not_fit_stop_the_run(world, monkeypatch, capfd, name, value):
    monkeypatch.setenv(name, value)
    world.add(6)
    assert job.main([]) == 1
    assert capfd.readouterr() == ("failed: settings: ValueError\n", "")
    assert world.read == [] and world.uploads == []


def test_the_defaults(monkeypatch):
    for name in list(ENV)[6:]:
        monkeypatch.delenv(name, raising=False)
    for name in list(ENV)[:6]:
        monkeypatch.setenv(name, ENV[name])
    got = job.read_settings(os.environ)
    assert (got.client_id, got.notice, got.limit, got.deployment) == ("", None, 5, "gpt-6-luna")
    assert got.caps == {"real": 1000, "seeded": 500}
    assert (job.CAP, job.SEEDED_CAP) == (1000, 500)


def test_the_settings_are_used(world, monkeypatch):
    monkeypatch.setenv("MODEL_DEPLOYMENT", "another")
    assert job.read_settings(os.environ).deployment == "another"
    assert job.read_settings(os.environ).notice == date(2026, 11, 1)


def test_no_hook_prints_a_message_or_a_traceback(world, capfd):
    assert job.main([]) == 0
    capfd.readouterr()
    error = ValueError(SECRET)
    sys.excepthook(ValueError, error, None)
    sys.unraisablehook(SimpleNamespace(exc_type=ValueError, exc_value=error, exc_traceback=None, err_msg=SECRET, object=SECRET))
    job.threading.excepthook(SimpleNamespace(exc_type=ValueError, exc_value=error, exc_traceback=None, thread=None))
    logging.getLogger("some.library").error(SECRET)
    out, err = capfd.readouterr()
    assert out == "failed: ValueError\n" * 3 and err == ""


def test_the_module_runs_and_a_bad_setting_ends_it_with_one_line():
    env = {k: v for k, v in os.environ.items() if k not in ENV} | {"PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    done = subprocess.run([sys.executable, "-m", "voltrieve_insights.job"], env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (1, "failed: settings: ValueError\n", "")


def test_a_library_that_warns_or_an_error_nobody_caught_says_nothing():
    code = (
        "import logging, warnings\n"
        "from voltrieve_insights import job\n"
        "job._silence()\n"
        f"logging.getLogger('azure.identity').warning('{SECRET}')\n"
        f"warnings.warn('{SECRET}')\n"
        f"raise ValueError('{SECRET}')\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert (done.returncode, done.stdout, done.stderr) == (1, "failed: ValueError\n", "")


def test_a_window_end_does_not_even_select_a_real_conversation(world, monkeypatch):
    chosen = []

    def seen(*args):
        chosen.extend(select(*args))
        return chosen

    select = job.select
    monkeypatch.setattr(job, "select", seen)
    world.add(6, started=datetime(2026, 11, 3, tzinfo=UTC))
    world.add(2, seeded=True, started=datetime(2026, 11, 3, tzinfo=UTC))
    assert job.main(["--window-end", "2026-11-08"]) == 0
    assert [c.seeded for c in chosen] == [True, True]


def test_a_group_call_of_each_source_runs_at_the_same_time(world, monkeypatch):
    """The deadline has room for the longest group calls only when they do not wait for each other."""
    running, most = [0], [0]

    async def group(*args, **kwargs):
        running[0] += 1
        most[0] = max(most[0], running[0])
        await asyncio.sleep(0.01)
        running[0] -= 1
        return []

    monkeypatch.setattr(job, "group", group)
    assert job.main([]) == 0
    assert most == [2]


def test_real_and_made_up_are_grouped_apart(world, monkeypatch):
    given = []
    real_group = groups.group

    async def group(model, pii, items, previous, **kwargs):
        given.append({i.seeded for i in items})
        return await real_group(model, pii, items, previous, **kwargs)

    monkeypatch.setattr(job, "group", group)
    world.add(6)
    world.add(6, seeded=True)
    assert job.main([]) == 0
    assert given == [{False}, {True}]
