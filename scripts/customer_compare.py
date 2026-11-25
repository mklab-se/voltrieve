# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["httpx", "httpx2", "mcp==2.3.0", "pyyaml", "pillow"]
# ///
"""A plain model with the tools of the customer's door, against Runa through the same door.

    uv run scripts/customer_compare.py [--count 8] [--only a,b] [--model gpt-6.1-terra] [--backend loop] [--dry-run]
                                       [--out docs/evidence/customer-door.txt]

The questions are from tests/routing/questions.yaml, in turn from docs, parts, curated and none. The plain
model runs in Foundry with nothing but the door's tools and the door's own instructions. A question costs
about 0.1 to 0.3 SEK, so a full run is approved first. --dry-run shows the questions, and calls nothing.

  right      every expected article number is in the answer, as tests/routing/check.py judges it
  invented   article numbers in the answer that do not exist
  word for word   the approved text is in the answer unchanged (curated questions)
  tokens     of the plain model. Those of Runa are in the chat.turn span, by the correlation id

Needs MCP_ALLOW_CLI, `az login` with the role Customer on the door, and rights to the model in Foundry.
Our turns are marked as tests, so they are not counted as use.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from itertools import chain, zip_longest
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ("scripts", "tests/routing", "agent_core/src")]

from check import ARTICLE, INDEX, QUESTIONS, expected_articles, message  # noqa: E402
from voltrieve_agent.curated import CURATED_ANSWERS  # noqa: E402
from voltrieve_client import TEST_HEADER, _az_token, azd_value, write_evidence  # noqa: E402

GROUPS = ("docs", "parts", "curated", "none")
MAX_STEPS = 8
PACE_SECONDS = 20.0  # ask_runa takes 5 a minute, and the door 30 calls
FOUNDRY_SCOPE = "https://ai.azure.com/.default"


@dataclass
class Result:
    who: str
    question: dict
    wanted: set[str]
    known: set[str] = field(repr=False)
    text: str = ""
    error: str = ""
    seconds: float = 0.0
    tokens: int | None = None
    calls: list[str] = field(default_factory=list)

    @property
    def mentioned(self) -> set[str]:
        return set(ARTICLE.findall(self.text))

    def row(self) -> str:
        topic = self.question.get("topic")
        right = f"{len(self.wanted & self.mentioned)} of {len(self.wanted)}" if self.wanted else "-"
        exact = "-" if topic is None else "yes" if CURATED_ANSWERS[topic].text in self.text else "NO"
        invented = ", ".join(sorted(self.mentioned - self.known)) or "0"
        return (f"{self.question['id']:22}{self.question['group']:9}{self.who:7}{right:>8}{invented:>10}{exact:>15}"
                f"{self.seconds:>9.1f}{self.tokens if self.tokens is not None else '-':>8}  "
                f"{self.error or ', '.join(self.calls)}")


def pick(questions: list[dict], count: int, only: str) -> list[dict]:
    if only:
        return [q for q in questions if q["id"] in only.split(",")]
    by_group = [[q for q in questions if q["group"] == group] for group in GROUPS]
    return [q for q in chain(*zip_longest(*by_group)) if q][:count]


async def plain(result: Result, ask: str, door, tools: list[dict], model: str, foundry: httpx.AsyncClient) -> None:
    """The model calls the tools until it answers. Nothing is stored in Foundry."""
    from doors_check import call

    items: list[dict] = [{"role": "user", "content": ask}]
    result.tokens = 0
    for _ in range(MAX_STEPS):
        r = await foundry.post("/openai/v1/responses", json={
            "model": model, "instructions": door.instructions or "", "input": items, "tools": tools, "store": False,
            "include": ["reasoning.encrypted_content"]})
        r.raise_for_status()
        response = r.json()
        result.tokens += (response.get("usage") or {}).get("total_tokens", 0)
        items += response["output"]
        wanted = [i for i in response["output"] if i["type"] == "function_call"]
        if not wanted:
            result.text = "".join(c.get("text", "") for i in response["output"] if i["type"] == "message"
                                  for c in i["content"])
            return
        for c in wanted:
            result.calls.append(c["name"])
            try:
                _, output = await call(door, c["name"], json.loads(c["arguments"] or "{}"))
            except Exception as exc:
                output = f"The call failed: {type(exc).__name__}"
            items.append({"type": "function_call_output", "call_id": c["call_id"], "output": output})
    result.error = f"no answer in {MAX_STEPS} steps"


async def runa(result: Result, ask: str, door) -> None:
    from doors_check import call

    ok, text = await call(door, "ask_runa", {"question": ask})
    result.text, result.error = (json.loads(text)["answer"], "") if ok else ("", text[:120])


async def timed(result: Result, work) -> Result:
    began = time.perf_counter()
    try:
        await work
    except Exception as exc:
        result.error = type(exc).__name__
    result.seconds = time.perf_counter() - began
    print(f"{result.question['id']} {result.who}: {result.seconds:.1f} s {result.error}", file=sys.stderr, flush=True)
    return result


async def compare(questions: list[dict], index: dict, model: str, backend: str) -> list[Result]:
    from doors_check import Door, token

    known = {p["article"] for p in index["parts"]}
    bearer = token("customer")
    foundry_headers = {"Authorization": f"Bearer {_az_token(FOUNDRY_SCOPE, 'rights in Foundry')}"}
    results = []
    session = Door("customer", bearer)
    session.http.headers.update({**TEST_HEADER, "X-Agent-Backend": backend})
    async with (httpx.AsyncClient(base_url=azd_value("FOUNDRY_PROJECT_ENDPOINT").rstrip("/"), headers=foundry_headers,
                                  timeout=120) as foundry, session as door):
        # Only what reads: the plain model must never book, and Runa is the one it is compared with.
        tools = [{"type": "function", "name": t.name, "description": t.description or "", "parameters": t.input_schema,
                  "strict": False} for t in (await door.list_tools()).tools if t.annotations.read_only_hint]
        for question in questions:
            began, ask = time.perf_counter(), message(question, index)
            session.http.headers["X-Correlation-Id"] = f"customer-compare-{question['id']}"
            for who, work in (("plain", lambda r: plain(r, ask, door, tools, model, foundry)),
                              ("Runa", lambda r: runa(r, ask, door))):
                result = Result(who, question, expected_articles(question, index), known)
                results.append(await timed(result, work(result)))
            await asyncio.sleep(max(0.0, PACE_SECONDS - (time.perf_counter() - began)))
    return results


def report(results: list[Result], model: str, backend: str) -> str:
    lines = [f"The customer's door: {model} with the door's tools, against Runa on {backend}", "",
             f"{'question':22}{'group':9}{'who':7}{'right':>8}{'invented':>10}{'word for word':>15}{'seconds':>9}"
             f"{'tokens':>8}  tools or error", *(r.row() for r in results), ""]
    for who in ("plain", "Runa"):
        mine = [r for r in results if r.who == who]
        parts, curated = [r for r in mine if r.wanted], [r for r in mine if r.question.get("topic")]
        lines.append(
            f"{who}: {sum(r.wanted <= r.mentioned for r in parts)} of {len(parts)} with every article number, "
            f"{sum(len(r.mentioned - r.known) for r in mine)} invented, "
            f"{sum(CURATED_ANSWERS[r.question['topic']].text in r.text for r in curated)} of {len(curated)} approved "
            f"texts word for word, {sum(bool(r.error) for r in mine)} errors")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--only", default="")
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--backend", default="loop")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    questions = pick(yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["questions"], args.count, args.only)
    if args.dry_run:
        for q in questions:
            print(f"{q['group']:9}{q['id']:22}{message(q, index)}")
        print(f"{len(questions)} questions, asked of {args.model} and of Runa on {args.backend}. Nothing was called.")
        return 0
    text = report(asyncio.run(compare(questions, index, args.model, args.backend)), args.model, args.backend)
    print(text)
    if args.out:
        write_evidence(args.out, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
