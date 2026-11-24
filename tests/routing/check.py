# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pyyaml", "pillow"]
# ///
"""Routing check: does each backend reach the right knowledge bases, and does it do so at once?

Each question says which sources it needs. For every backend and repeat this asks the deployed
API and reads the trace:

  right     the sources used are exactly the expected ones, and every tool the question lists
            (such as check_part_stock) was called
  vain      a source was used that the question did not need
  missed    a needed source was not used
  together  a two source question reached both within one model step, with overlapping calls
  steps     model calls in the turn. Fewer is faster for the user.
  articles  for parts questions, every expected article number is in the answer
  invented  an article number in the answer that does not exist
  mentions  words the answer must contain, such as the assistant's name, count towards right

    uv run tests/routing/check.py [--backends prompt,loop,hosted] [--repeats 3] [--only a,b] [--workers 1]
    uv run tests/routing/check.py --photos [--limit 20]

Writes docs/evidence/routing.txt and routing.json unless --no-save. Exits 1 when any question is
below 100 percent on routing or articles. Needs `azd env select` and `az login` with Chat.User.

--photos asks the questions of tests/photos/questions.yaml instead: a drawn parking sign, a Spark with
a cracked headlamp or an undamaged one, uploaded with the question. Each says what the answer must
be: yes or no at the start (`verdict`), the right article number and not the one of the other side
(`article`, `no_article`), words it must have (`mentions`), or that the upload is refused
(`rejected`). The run also prints tokens, an estimated cost and the time of the upload. A turn that
fails with model_error, model_failed, agent_unavailable or a busy upload (503) is asked again after
30 s and 60 s, at most twice, and is left out of the medians; one that still fails is an error, not
a wrong answer. An answer with no clear yes or no ("Yes, but only...") is printed in full. A photo that the server lost
(it scaled to zero) is uploaded again, and is not a wrong answer. --limit N runs the first N turns,
for a first look at the cost. It writes photos-routing.txt and photos-routing.json instead.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "photos"))

from answers import car_context, read_verdict  # noqa: E402
from prices import (  # noqa: E402  prices, with their date and source, are in tests/photos/prices.py
    AGENT_MODEL, PLANNING_MODEL, PRICES_DATE, PRICES_SOURCE, PRICES_USD_PER_MILLION,
    SEARCH_REASONING_USD_PER_MILLION, token_cost,
)
from voltrieve_client import Api, ApiError, Turn, api_token, api_url, cached_token, write_evidence  # noqa: E402

QUESTIONS = Path(__file__).with_name("questions.yaml")
PHOTO_QUESTIONS = ROOT / "tests" / "photos" / "questions.yaml"
# What the API says when the model or the agent fails for a moment (agent_core loop.py, api backends/hosted.py),
# and when the upload queue is full (503 busy). Such a turn is asked again after each wait, at most twice.
RETRYABLE = ("model_error", "model_failed", "agent_unavailable", "busy")
RETRY_WAITS = (30, 60)
TTFT_LIMIT = 10.0
INDEX = ROOT / "data" / "generated" / "parts-index.json"
EVIDENCE = ROOT / "docs" / "evidence"
ARTICLE = re.compile(r"VP-\d{5}")
SOURCES = {
    "get_curated_answer": "curated",
    "search_voltrieve_docs": "docs",
    "search_spare_parts": "parts",
    "voltrieve-kb": "docs",
    "voltrieve-parts-kb": "parts",
}


@dataclass
class Run:
    backend: str
    question: str
    group: str
    repeat: int
    seconds: float = 0.0
    first_text_seconds: float | None = None
    first_status_seconds: float | None = None
    used: list[str] = field(default_factory=list)
    calls: int = 0
    steps: int = 0
    together: bool | None = None
    saved_ms: int = 0
    articles_expected: int = 0
    articles_found: int = 0
    invented: list[str] = field(default_factory=list)
    error: str = ""
    answer: str = ""
    expected: list[str] = field(default_factory=list)
    topic: str | None = None
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    planning_input_tokens: int = 0
    planning_output_tokens: int = 0
    search_reasoning_tokens: int = 0
    searches: int = 0
    expected_topic: str | None = None
    final: bool = False
    tools: list[str] = field(default_factory=list)  # every server tool the turn called
    expected_tools: list[str] = field(default_factory=list)  # tools the question needs, not searches
    unmentioned: list[str] = field(default_factory=list)  # words the answer should have had
    # Photo questions
    photo: bool = False
    expected_verdict: str | None = None
    verdict: str | None = None
    forbidden: list[str] = field(default_factory=list)  # article numbers the answer must not have
    free_sources: bool = False  # the sources are not judged
    expect_rejected: str | None = None  # the code the upload must be refused with
    rejected: str | None = None
    upload_seconds: float | None = None
    waited: bool = False  # was retried after a failure: left out of the medians
    waited_seconds: float = 0.0
    retried_after: list[str] = field(default_factory=list)  # the code of each failure that was retried
    planning_model: str = ""  # the model Search planned with, from the trace
    reuploaded: bool = False  # the server lost the photo and it was uploaded again

    @property
    def right(self) -> bool:
        if self.error:
            return False
        if self.expect_rejected:
            return self.rejected == self.expect_rejected
        sources = self.free_sources or (
            sorted(self.used) == sorted(self.expected)
            and self.topic == self.expected_topic
            and set(self.expected_tools) <= set(self.tools)
        )
        return (
            sources
            and (self.expected_verdict is None or self.verdict == self.expected_verdict)
            and not self.unmentioned
            and not self.forbidden
        )

    @property
    def vain(self) -> list[str]:
        return sorted(set(self.used) - set(self.expected))

    @property
    def missed(self) -> list[str]:
        return sorted(set(self.expected) - set(self.used))


def cost_usd(run: Run) -> float | None:
    """What the tokens of a turn cost at list price: the agent's, Search's planning and its
    reasoning. None when the model has no price. An estimate, not the bill."""
    agent = token_cost(PRICES_USD_PER_MILLION, AGENT_MODEL, run.input_tokens, run.cached_tokens, run.output_tokens)
    if agent is None:
        return None
    planner = run.planning_model if run.planning_model in PRICES_USD_PER_MILLION else PLANNING_MODEL
    planning = token_cost(
        PRICES_USD_PER_MILLION, planner, run.planning_input_tokens, 0, run.planning_output_tokens
    ) or 0.0
    return agent + planning + run.search_reasoning_tokens * SEARCH_REASONING_USD_PER_MILLION / 1e6


def overlap(calls: list[dict]) -> bool:
    timed = [c for c in calls if c.get("started_ms") is not None and c.get("finished_ms") is not None]
    timed.sort(key=lambda c: c["started_ms"])
    return any(b["started_ms"] < a["finished_ms"] for a, b in zip(timed, timed[1:]))


def read_trace(run: Run, turn) -> None:
    run.tools = sorted({c["tool"] for c in turn.trace("server_tool_called") if c.get("tool")})
    server = [c for c in turn.trace("server_tool_called") if c.get("tool") in SOURCES]
    run.topic = next((json.loads(c.get("arguments") or "{}").get("topic") for c in server
                      if SOURCES[c["tool"]] == "curated"), None)
    run.final = bool(turn.trace("final_answer_sent"))
    usage = (turn.done or {}).get("usage", {})
    run.input_tokens = usage.get("input_tokens", 0)
    run.cached_tokens = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    run.output_tokens = usage.get("output_tokens", 0)
    retrieved = turn.trace("knowledge_base_retrieved")
    run.planning_input_tokens = sum(k.get("planning_input_tokens", 0) for k in retrieved)
    run.planning_output_tokens = sum(k.get("planning_output_tokens", 0) for k in retrieved)
    run.planning_model = next((k["planning_model"] for k in reversed(retrieved) if k.get("planning_model")), "")
    run.search_reasoning_tokens = sum(k.get("search_reasoning_tokens", 0) for k in retrieved)
    run.searches = len(retrieved) + len(turn.trace("mcp_tool_called"))
    mcp = [c for c in turn.trace("mcp_tool_called") if c.get("server") in SOURCES]
    run.used = sorted({SOURCES[c["tool"]] for c in server} | {SOURCES[c["server"]] for c in mcp})
    run.calls = len(server) + len(mcp)
    run.steps = len(turn.trace("model_called"))
    if len(run.expected) > 1:
        if server:
            by_step: dict[int, set[str]] = {}
            for call in server:
                by_step.setdefault(call.get("iteration", 0), set()).add(SOURCES[call["tool"]])
            run.together = any(len(s) > 1 for s in by_step.values()) and bool(
                turn.trace("tools_parallel_started")
            )
        else:
            run.together = overlap(mcp)
    finished = turn.trace("tools_parallel_finished")
    run.saved_ms = sum(f.get("one_by_one_ms", 0) - f.get("wall_ms", 0) for f in finished)


def expected_articles(question: dict, index: dict) -> set[str]:
    wanted = set(index["jobs"].get(question.get("job", ""), []))
    wanted |= set(index["jobs"].get(question.get("also_job", ""), []))
    target = question.get("article")
    if target:
        wanted |= {
            p["article"] for p in index["parts"]
            if p["key"] == target["key"] and target["fits"] in p["fits"]
        }
    return wanted


STOCK = ROOT / "services" / "dealer" / "app" / "parts-stock.json"


def message(question: dict, index: dict) -> str:
    if question.get("pick") == "out_everywhere":
        stock = json.loads(STOCK.read_text(encoding="utf-8"))["stock"]
        return question["ask"].format(article=next(a for a, by in stock.items() if not any(by.values())))
    reverse = question.get("reverse")
    if not reverse:
        return question["ask"]
    part = next(
        p for p in index["parts"] if p["key"] == reverse["key"] and reverse["fits"] in p["fits"]
    )
    return question["ask"].format(article=part["article"])


def photo_bytes(photo: dict) -> bytes:
    """The picture a question names: a drawn sign, a Spark with a cracked lamp, the undamaged Spark or a
    blurred sign. Made from code and the repository's own pictures."""
    import pictures
    import signs

    (kind, value), = photo.items()
    return {
        "sign": signs.draw, "lamp": pictures.broken_lamp, "blurred": pictures.blurred,
        "car": lambda _: pictures.undamaged(),
    }[kind](value)


def articles_of(target: dict, index: dict) -> set[str]:
    return {p["article"] for p in index["parts"] if p["key"] == target["key"] and target["fits"] in p["fits"]}


def retry_code(turn: Turn | None, exc: ApiError | None) -> str | None:
    """The code of a failure that is worth asking again after, or None."""
    if exc is not None:
        if exc.status == 429:
            return "http_429"
        return "busy" if exc.status == 503 and "busy" in exc.detail else None
    code = (turn.error or {}).get("code") if turn is not None else None
    return code if code in RETRYABLE else None


def send(api: Api, run: Run, question: dict, index: dict, patient: bool) -> Turn | None:
    """One turn, with its photo when the question has one. With `patient`, a failure that is worth
    asking again after is waited out (30 s, then 60 s) and the turn asked again, at most twice; the run
    says what it was retried after. A failure that stays is an error. Returns None when the photo was
    rejected."""
    cid = f"routing-{run.backend}-{question['id']}-{run.repeat}"
    waits = iter(RETRY_WAITS)
    while True:
        began = time.perf_counter()
        turn, failed = None, None
        try:
            if "photo" in question:
                context = question.get("context")
                result = api.turn_with_photo(
                    message(question, index), photo_bytes(question["photo"]), "photo", correlation_id=cid,
                    context=car_context(context["when"]) if context else None,
                )
                if run.upload_seconds is None:  # the first upload, not a retry's
                    run.upload_seconds = result.upload_seconds
                run.reuploaded = run.reuploaded or result.reuploaded
                if result.turn is None:
                    run.rejected = result.upload.get("code")
                    if not question.get("rejected"):
                        run.error = f"upload {result.upload.get('status')}: {run.rejected}"
                    return None
                turn = result.turn
                run.seconds = result.turn_seconds
            else:
                turn = api.turn(message(question, index), correlation_id=cid)
                run.seconds = round(time.perf_counter() - began, 2)
        except ApiError as exc:
            failed = exc
            if not patient or retry_code(None, exc) is None:
                raise
        code = retry_code(turn, failed)
        if not patient or code is None:
            return turn
        wait = next(waits, None)
        if wait is None:
            if turn is None:
                raise failed
            return turn
        run.waited, run.waited_seconds = True, run.waited_seconds + wait
        run.retried_after.append(code)
        print(f"{run.backend} {run.question} #{run.repeat}: retried after {code}", file=sys.stderr, flush=True)
        time.sleep(wait)


def ask(url, token, backend: str, question: dict, repeat: int, index: dict, patient: bool = False) -> Run:
    run = Run(backend, question["id"], question["group"], repeat, expected=question["expect"],
              expected_topic=question.get("topic"), expected_tools=question.get("tools", []))
    run.photo = "photo" in question
    run.expected_verdict = question.get("verdict")
    run.free_sources = bool(question.get("free_sources"))
    run.expect_rejected = question.get("rejected")
    wanted = expected_articles(question, index)
    known = {p["article"] for p in index["parts"]}
    forbidden = question.get("no_article")
    api = Api(url, token, backend)
    try:
        turn = send(api, run, question, index, patient)
    except ApiError as exc:
        run.error = str(exc)
        return run
    finally:
        api.close()
    if turn is None:
        return run
    if turn.error:
        run.error = str(turn.error.get("code"))
    run.answer = turn.text
    run.unmentioned = [w for w in question.get("mentions", []) if w.lower() not in turn.text.lower()]
    run.first_text_seconds = turn.first_text_seconds
    run.first_status_seconds = turn.first_status_seconds
    read_trace(run, turn)
    mentioned = set(ARTICLE.findall(turn.text))
    run.articles_expected = len(wanted)
    run.articles_found = len(wanted & mentioned)
    run.invented = sorted(mentioned - known)
    if run.expected_verdict:
        run.verdict = read_verdict(turn.text)
    if forbidden:
        run.forbidden = sorted(mentioned if forbidden == "any" else mentioned & articles_of(forbidden, index))
    return run


def percent(part: int, whole: int) -> str:
    return f"{100 * part // whole} %" if whole else "n/a"


def median(values: list[float]) -> float:
    return round(statistics.median(values), 1) if values else 0.0


def photo_report(runs: list[Run], backends: list[str], warm: dict[str, float] | None) -> list[str]:
    """What the photo turns showed. Turns that were retried are left out of the medians."""
    lines = ["", "Photo turns per backend (turns that were retried after a failure are left out of the medians)", ""]
    lines.append(f"{'backend':8}{'turns':>6}{'right':>7}{'verdicts':>10}{'upload s':>10}{'first text s':>14}"
                 f"{'seconds':>9}{'over 10 s':>11}{'tokens in/out':>16}  notes")
    for backend in backends:
        rows = [r for r in runs if r.backend == backend and r.photo]
        if not rows:
            continue
        timed = [r for r in rows if not r.waited and not r.error]
        asked = [r for r in rows if r.expected_verdict and not r.error]
        chats = [r for r in timed if not r.expect_rejected]
        slow = sum(r.first_text_seconds is not None and r.first_text_seconds > TTFT_LIMIT for r in chats)
        notes = (f"{sum(r.waited for r in rows)} waited, {sum(r.reuploaded for r in rows)} re-uploaded, "
                 f"{sum(bool(r.error) for r in rows)} errors")
        lines.append(
            f"{backend:8}{len(rows):>6}{percent(sum(r.right for r in rows), len(rows)):>7}"
            f"{sum(r.verdict == r.expected_verdict for r in asked):>5} of {len(asked):<3}"
            f"{median([r.upload_seconds for r in timed if r.upload_seconds is not None]):>10}"
            f"{median([r.first_text_seconds for r in chats if r.first_text_seconds is not None]):>14}"
            f"{median([r.seconds for r in chats]):>9}{slow:>11}"
            f"{int(statistics.median([r.input_tokens for r in chats] or [0])):>9}/"
            f"{int(statistics.median([r.output_tokens for r in chats] or [0])):<6}  {notes}"
        )
    lines += ["", "Tokens and estimated cost", ""]
    for backend in backends:
        rows = [r for r in runs if r.backend == backend and r.photo and not r.expect_rejected]
        if not rows:
            continue
        costs = [cost_usd(r) for r in rows]
        tokens = f"{sum(r.input_tokens for r in rows)} in ({sum(r.cached_tokens for r in rows)} cached), "
        tokens += f"{sum(r.output_tokens for r in rows)} out"
        if None in costs:
            lines.append(f"{backend:8}{len(rows)} turns, {tokens} tokens, no price for the model")
        else:
            total = sum(costs)
            lines.append(f"{backend:8}{len(rows)} turns, {tokens} tokens, about USD {total:.4f} "
                         f"(USD {1000 * total / len(rows):.2f} per 1,000 turns)")
    lines.append(f"Estimated at the list prices of {AGENT_MODEL} read {PRICES_DATE} ({PRICES_SOURCE}), "
                 "planning and Search reasoning included, cache writes left out. An estimate, not the bill.")
    for r in runs:
        if r.retried_after:
            lines.append(f"{r.backend} {r.question} #{r.repeat}: retried after {', '.join(r.retried_after)}")
    unclear = [r for r in runs if r.photo and r.expected_verdict and not r.error and r.verdict is None]
    if unclear:
        lines += ["", f"Unclear answers ({len(unclear)}): no clear yes or no, judge them by hand", ""]
        for r in unclear:
            lines += [f"{r.backend} {r.question} #{r.repeat} (expected {r.expected_verdict}): {r.answer}", ""]
    if warm:
        lines += ["", "Start, one ordinary turn before the photos (not in the medians): "
                  + ", ".join(f"{b} {s} s" for b, s in warm.items())]
    return lines


def report(runs: list[Run], backends: list[str], warm: dict[str, float] | None = None) -> str:
    lines = ["Routing per backend and question group", ""]
    lines.append(f"{'backend':8}{'group':7}{'runs':>5}{'right':>7}{'vain':>6}{'missed':>8}"
                 f"{'together':>10}{'steps':>7}{'calls':>7}{'seconds':>9}")
    for backend in backends:
        extra = list(dict.fromkeys(r.group for r in runs))
        for group in dict.fromkeys(("curated", "none", "docs", "parts", "both", "stock", *extra)):
            rows = [r for r in runs if r.backend == backend and r.group == group]
            if not rows:
                continue
            pairs = [r for r in rows if r.together is not None]
            lines.append(
                f"{backend:8}{group:7}{len(rows):>5}"
                f"{percent(sum(r.right for r in rows), len(rows)):>7}"
                f"{sum(bool(r.vain) for r in rows):>6}{sum(bool(r.missed) for r in rows):>8}"
                f"{percent(sum(bool(r.together) for r in pairs), len(pairs)):>10}"
                f"{median([r.steps for r in rows]):>7}{median([r.calls for r in rows]):>7}"
                f"{median([r.seconds for r in rows if not r.waited]):>9}"
            )
    lines += ["", "Article numbers in the answers", ""]
    for backend in backends:
        rows = [r for r in runs if r.backend == backend and r.articles_expected]
        expected = sum(r.articles_expected for r in rows)
        found = sum(r.articles_found for r in rows)
        complete = sum(r.articles_found == r.articles_expected for r in rows)
        invented = sum(len(r.invented) for r in rows)
        lines.append(f"{backend:8}{found} of {expected} found, {complete} of {len(rows)} answers "
                     f"complete, {invented} invented")
    if any(r.photo for r in runs):
        lines += photo_report(runs, backends, warm)
    saved = [r.saved_ms for r in runs if r.saved_ms]
    if saved:
        lines += ["", f"Parallel tool runs saved a median of {median(saved) / 1000} s "
                      f"over {len(saved)} turns (own loop and hosted agent)."]
    lines += ["", "Runs that were not right", ""]
    for r in runs:
        partial = r.articles_found != r.articles_expected
        if not r.right or r.together is False or partial or r.invented:
            lines.append(
                f"{r.backend} {r.question} #{r.repeat}: used {r.used}, expected {r.expected}, "
                f"together {r.together}, articles {r.articles_found}/{r.articles_expected}, "
                f"invented {r.invented}, missing words {r.unmentioned}, error {r.error or 'none'}"
                + (f", verdict {r.verdict} for {r.expected_verdict}, forbidden {r.forbidden}" if r.photo else "")
            )
    return "\n".join(lines) + "\n"


PHOTO_FIELDS = ("photo", "expected_verdict", "verdict", "forbidden", "free_sources", "expect_rejected", "rejected",
                "upload_seconds", "waited", "waited_seconds", "reuploaded", "retried_after", "planning_model")


def make_jobs(questions: list[dict], backends: list[str], repeats: int, limit: int | None) -> list[tuple]:
    """Repeat by repeat, question by question, backend by backend. `limit` keeps the first turns."""
    jobs = [(b, q, r + 1) for r in range(repeats) for q in questions for b in backends]
    return jobs if limit is None else jobs[:limit]


def warm_up(url: str, token, backends: list[str]) -> dict[str, float]:
    """One ordinary turn per backend, so that a hosted agent that was asleep is awake before the photos.
    The time is its start, and it is reported apart from the photo turns."""
    started = {}
    for backend in backends:
        api = Api(url, token, backend)
        began = time.perf_counter()
        try:
            api.turn("Good morning!", correlation_id=f"routing-{backend}-warm-up")
        except ApiError as exc:
            print(f"warm-up {backend}: {exc}", file=sys.stderr, flush=True)
        finally:
            api.close()
        started[backend] = round(time.perf_counter() - began, 1)
        print(f"warm-up {backend}: {started[backend]} s", file=sys.stderr, flush=True)
    return started


def dump(runs: list[Run], photos: bool) -> str:
    """The runs as JSON. The plain suite keeps its own fields, so its file does not change."""
    rows = [asdict(r) for r in runs]
    if not photos:
        rows = [{k: v for k, v in row.items() if k not in PHOTO_FIELDS} for row in rows]
    return json.dumps(rows, indent=1, ensure_ascii=False).replace("\u2014", "-") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backends", default="prompt,loop,hosted")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", default="")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--label", default="", help="save only to docs/evidence/models/<label>.json")
    parser.add_argument("--photos", action="store_true",
                        help="ask the photo questions of tests/photos/questions.yaml instead")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N turns")
    parser.add_argument("--no-warm-up", action="store_true", help="with --photos: no ordinary turn first")
    args = parser.parse_args()

    questions = yaml.safe_load((PHOTO_QUESTIONS if args.photos else QUESTIONS).read_text(encoding="utf-8"))["questions"]
    if args.only:
        questions = [q for q in questions if q["id"] in args.only.split(",")]
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    backends = args.backends.split(",")
    url = api_url()
    token = cached_token(api_token)
    jobs = make_jobs(questions, backends, args.repeats, args.limit)
    warm = warm_up(url, token, backends) if args.photos and not args.no_warm_up else None

    def timed(position: int, job: tuple) -> Run:
        run = ask(url, token, job[0], job[1], job[2], index, patient=args.photos)
        print(f"{position + 1}/{len(jobs)} {run.backend} {run.question} {run.seconds} s "
              f"{run.used} {run.error}", file=sys.stderr, flush=True)
        return run

    with ThreadPoolExecutor(args.workers) as pool:
        runs = list(pool.map(timed, range(len(jobs)), jobs))
    text = report(runs, backends, warm)
    print(text)
    if args.label:
        folder = EVIDENCE / "models"
        folder.mkdir(exist_ok=True)
        (folder / f"{args.label}.json").write_text(dump(runs, args.photos), encoding="utf-8")
    elif not args.no_save:
        name = "photos-routing" if args.photos else "routing"
        write_evidence(EVIDENCE / f"{name}.txt", text)
        (EVIDENCE / f"{name}.json").write_text(dump(runs, args.photos), encoding="utf-8")
    failed = [r for r in runs if not r.right or r.articles_found != r.articles_expected or r.invented]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
