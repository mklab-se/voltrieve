"""One run of the usage insights: read the chats of the last settled week, and save totals.

    python -m voltrieve_insights.job [--window-end YYYY-MM-DD]

It fails closed. Every step has to work before the one upload at the end, and a run that fails saves
nothing and ends with exit code 1. It prints one line a step, with counts, and for a failure the step and
the type of the error. It never prints a message, a traceback or a line of a library: they could hold text.

--window-end is the last day, included, of a week to run for made up conversations only. Such a run
reads no real chat and writes no real row.
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import threading
import warnings
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import httpx
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider

from voltrieve_insights.conversations import PARALLEL, Conversation, list_raw, read_text, sample, select, window
from voltrieve_insights.groups import PersonalData, _all, group
from voltrieve_insights.labels import DEPLOYMENT, TOKEN_SCOPE, Labelled, Model, Refused, label
from voltrieve_insights.quiet import hush
from voltrieve_insights.store import (
    INGESTION_SCOPE,
    QUERY_SCOPE,
    SEEDED,
    SOURCES,
    previous_names,
    save,
    table_rows,
)
from voltrieve_insights.totals import MIN_PEOPLE, totals

PROJECT_SCOPE = "https://ai.azure.com/.default"
DEADLINE = 25 * 60  # seconds: the platform stops the job after 30 minutes
TIMEOUT = 60.0  # seconds, for one try of a request
CAP = 2_000  # conversations of each source
MAX_REFUSED = 10  # percent of the conversations sent to be labelled
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


class TooManyRefused(Exception):
    """The service refused so many conversations that the totals would mislead."""


@dataclass(frozen=True)
class Settings:
    project: str
    models: str
    language: str
    ingestion: str
    rule: str
    workspace: str
    client_id: str
    notice: date | None
    limit: int
    cap: int
    deployment: str


def _day(text: str) -> date:
    if not _DAY.fullmatch(text):
        raise ValueError("not a day")
    return date.fromisoformat(text)


def read_settings(env: Mapping[str, str]) -> Settings:
    def given(name: str) -> str:
        return env.get(name, "").strip()

    def needed(name: str) -> str:
        if not (value := given(name)):
            raise ValueError("a setting is missing")
        return value

    def endpoint(name: str) -> str:
        value = needed(name)
        if not value.startswith("https://"):  # a token goes there
            raise ValueError("an endpoint is not https")
        return value

    notice, cap = given("INSIGHTS_NOTICE_DATE"), int(given("INSIGHTS_CAP") or CAP)
    if cap < 1:
        raise ValueError("the cap is too low")
    return Settings(
        project=endpoint("FOUNDRY_PROJECT_ENDPOINT"),
        models=endpoint("FOUNDRY_OPENAI_ENDPOINT"),
        language=endpoint("LANGUAGE_ENDPOINT"),
        ingestion=endpoint("INSIGHTS_INGESTION_ENDPOINT"),
        rule=needed("INSIGHTS_RULE_ID"),
        workspace=needed("LOG_ANALYTICS_WORKSPACE_ID"),
        client_id=given("AZURE_CLIENT_ID"),
        notice=_day(notice) if notice else None,
        limit=max(int(given("INSIGHTS_LIMIT") or MIN_PEOPLE), MIN_PEOPLE),
        cap=cap,
        deployment=given("MODEL_DEPLOYMENT") or DEPLOYMENT,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _credential(client_id: str):
    return ManagedIdentityCredential(client_id=client_id) if client_id else DefaultAzureCredential()


class Bearer(httpx.Auth):
    """A token for each request. The provider keeps it and renews it, so a long run does not outlive it."""

    def __init__(self, token: Callable[[], str]):
        self._token = token

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = None
        try:
            token = await asyncio.to_thread(self._token)
        except Exception:  # its text may name an account
            pass
        if token is None:
            raise httpx.TransportError("no token")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


class Progress:
    """The step that runs now, and a line when it is done."""

    def __init__(self) -> None:
        self.step = "settings"

    def start(self, step: str) -> None:
        self.step = step

    def done(self, text: str) -> None:
        print(f"{self.step}: {text}", flush=True)


async def _eight[T, R](work: Callable[[T], Awaitable[R]], items: list[T]) -> list[R]:
    """The work of each item in order, eight at a time. The first failure stops the rest."""
    gate = asyncio.Semaphore(PARALLEL)

    async def one(item: T) -> R:
        async with gate:
            return await work(item)

    return await _all([one(item) for item in items])


def _counts(by_source: Mapping[str, list]) -> str:
    return ", ".join(f"{len(found)} {source}" for source, found in by_source.items())


async def _run(settings: Settings, now: datetime, window_end: date | None, progress: Progress) -> None:
    if window_end is None:
        (first, last), sources, notice = window(now), SOURCES, settings.notice
    else:  # without a notice date no real conversation is selected
        (first, last), sources, notice = (window_end - timedelta(days=6), window_end), (SEEDED,), None
    credential = _credential(settings.client_id)

    def token(scope: str) -> Callable[[], str]:
        return get_bearer_token_provider(credential, scope)

    def client(scope: str, base: str = "") -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base, auth=Bearer(token(scope)), timeout=TIMEOUT)

    async with (
        asyncio.timeout(DEADLINE),
        client(PROJECT_SCOPE, settings.project) as project,
        client(QUERY_SCOPE) as logs,
        client(INGESTION_SCOPE) as ingestion,
        httpx.AsyncClient(timeout=TIMEOUT) as plain,
    ):
        ai = token(TOKEN_SCOPE)
        model = Model(plain, settings.models, ai, settings.deployment)
        pii = PersonalData(plain, settings.language, ai)

        progress.start("list")
        raw = [item async for item in list_raw(project, datetime.combine(first, time(), UTC))]
        progress.done(f"{len(raw)}")

        progress.start("select")
        chosen = select(raw, first, last, notice)
        picked = {source: [c for c in chosen if c.seeded == (source == SEEDED)] for source in sources}
        progress.done(_counts(picked))

        progress.start("sample")
        sampled = {source: sample(found, settings.cap) for source, found in picked.items()}
        shares = {source: share for source, (_, share) in sampled.items()}
        picked = {source: found for source, (found, _) in sampled.items()}
        progress.done(_counts(picked))

        progress.start("read")
        everything = [c for found in picked.values() for c in found]
        texts = await _eight(lambda c: read_text(project, c.id), everything)
        with_text = [
            (c, messages, registrations)
            for c, (messages, registrations) in zip(everything, texts, strict=True)
            if any(role == "user" for role, _ in messages)
        ]
        progress.done(f"{len(with_text)} with user text of {len(everything)}")

        progress.start("label")

        async def one(item: tuple[Conversation, list[tuple[str, str]], tuple[str, ...]]) -> Labelled | None:
            try:
                return await label(model, *item)
            except Refused:
                return None

        labelled = [found for found in await _eight(one, with_text) if found is not None]
        refused = len(with_text) - len(labelled)
        if refused * 100 > len(with_text) * MAX_REFUSED:
            raise TooManyRefused
        progress.done(f"{len(labelled)} labelled, {refused} refused")

        progress.start("previous names")
        previous = {source: await previous_names(logs, settings.workspace, source) for source in sources}
        progress.done(", ".join(f"{sum(map(len, names.values()))} {source}" for source, names in previous.items()))

        progress.start("group")
        refused_groups: list[int] = []  # calls, for each source
        by_source = {source: [item for item in labelled if item.seeded == (source == SEEDED)] for source in sources}
        results = await _all(
            [group(model, pii, by_source[source], previous[source], refused=refused_groups) for source in sources]
        )
        grouped = dict(zip(sources, results, strict=True))
        named = sum(name is not None for items in results for _, name in items)
        progress.done(f"{named} in a group, {sum(refused_groups)} calls refused")

        progress.start("totals")
        rows = []
        for source in sources:
            found = totals(grouped[source], settings.limit)
            rows += table_rows(found, now, first, last, source, shares[source])
        progress.done(f"{len(rows)} rows")

        progress.start("save")
        await save(ingestion, settings.ingestion, settings.rule, rows)
        progress.done(f"{len(rows)} rows, {model.input_tokens} tokens in, {model.output_tokens} out")


def _failed(kind: type[BaseException]) -> None:
    print(f"failed: {kind.__name__}", flush=True)


def _silence() -> None:
    """Nothing but our own lines: no library's log or warning, and no traceback, wherever it comes from."""
    hush()
    logging.getLogger().addHandler(logging.NullHandler())  # or the last resort prints warnings to stderr
    warnings.simplefilter("ignore")
    sys.excepthook = lambda kind, value, trace: _failed(kind)
    sys.unraisablehook = lambda what: _failed(what.exc_type)
    threading.excepthook = lambda what: _failed(what.exc_type)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--window-end", metavar="YYYY-MM-DD", help="made up conversations only, of the week to this day")
    args = parser.parse_args(argv)
    _silence()
    progress, failed = Progress(), None
    try:
        window_end = None if args.window_end is None else _day(args.window_end)
        asyncio.run(_run(read_settings(os.environ), _now(), window_end, progress))
    except BaseException as error:  # its message and its causes may hold text
        failed = type(error).__name__
    if failed is None:
        return 0
    print(f"failed: {progress.step}: {failed}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
