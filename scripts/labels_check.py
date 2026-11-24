# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["voltrieve-insights", "httpx", "pyyaml", "azure-identity"]
#
# [tool.uv]
# exclude-newer = "2026-11-17T00:00:00Z"
#
# [tool.uv.sources]
# voltrieve-insights = { path = "../services/insights" }
# voltrieve-agent-core = { path = "../agent_core" }
# ///
"""Are the labels right? Mask, label and filter the made up conversations, all in memory.

    uv run scripts/labels_check.py [--out docs/evidence/insights-labels.txt]

Reads services/insights/data/seeded.yaml and nothing from Foundry's conversations. The 100 labelled
ones give two numbers: the share with topic, car model, kind and result all right, and the share per
label. The 50 with planted personal details give how many reached an intent, before and after the
filter. Only made up text is shown. A conversation the model failed on counts as wrong.
Needs az, a role that may call the model, and FOUNDRY_OPENAI_ENDPOINT.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from model_costs import PRICES  # noqa: E402
from seed_conversations import load  # noqa: E402
from voltrieve_client import azd_value, write_evidence  # noqa: E402
from voltrieve_insights.conversations import Conversation  # noqa: E402
from voltrieve_insights.labels import DEPLOYMENT, TOKEN_SCOPE, Labelled, Model, ModelError, label  # noqa: E402
from voltrieve_insights.quiet import hush  # noqa: E402

LABELS = ("topic", "car_model", "kind", "result")
LONG_WORD = 5  # letters a word of a planted detail needs to count on its own


@dataclass(frozen=True)
class Found:
    item: dict
    got: Labelled | None  # None when the model failed
    raw_intent: str | None  # what the model wrote, before the filter
    error: str | None = None

    @property
    def wrong(self) -> list[str]:
        return [name for name in LABELS if self.got is None or getattr(self.got, name) != self.item[name]]


class _Seen:
    """Passes a question on to the model and keeps the answer, to see the intent before the filter."""

    def __init__(self, model: Model):
        self._model = model
        self.answer: dict = {}

    async def ask(self, system: str, data: str, schema: dict) -> dict:
        self.answer = await self._model.ask(system, data, schema)
        return self.answer


async def _one(model: Model, item: dict) -> Found:
    conversation = Conversation(
        item["id"], f"seeded-{item['id']}", item["client"], "loop", item.get("stated"), dt.date.today(), True
    )
    messages = [("assistant" if n % 2 else "user", text) for n, text in enumerate(item["said"])]
    registrations = (item["registration"],) if "registration" in item else ()
    seen = _Seen(model)
    try:
        got = await label(seen, conversation, messages, registrations)
    except ModelError as exc:
        return Found(item, None, None, str(exc))
    return Found(item, got, seen.answer["intent"])


async def check(model: Model, items: list[dict]) -> list[Found]:
    return list(await asyncio.gather(*(_one(model, item) for item in items)))


def _squash(text: str) -> str:
    return "".join(c for c in text.casefold() if c.isalnum())


def reached(detail: str, intent: str) -> bool:
    """Whether a planted detail is in an intent: whole, or one long word of a name or an address."""
    if _squash(detail) in _squash(intent):
        return True
    if "@" in detail:
        return False
    words = {w.casefold() for w in re.findall(r"[^\W\d_]+", intent)}
    return any(len(w) >= LONG_WORD and w.casefold() in words for w in re.findall(r"[^\W\d_]+", detail))


def _share(part: int, whole: int) -> str:
    return f"{part} of {whole}" + (f" ({100 * part / whole:.0f} %)" if whole else "")


def _accuracy(found: list[Found]) -> list[str]:
    lines = ["Labels", f"  all four right: {_share(sum(not f.wrong for f in found), len(found))}"]
    for name in LABELS:
        lines.append(f"  {name}: {_share(sum(name not in f.wrong for f in found), len(found))}")
    steering = [f for f in found if f.item.get("steers")]
    lines.append(f"  all four right where the person tries to steer: {_share(sum(not f.wrong for f in steering), len(steering))}")
    lines += ["", "Wrong"]
    for f in found:
        if f.got is None:
            lines.append(f"  {f.item['id']}  error: {f.error}")
            continue
        lines += [
            f"  {f.item['id']}  {name}: got {getattr(f.got, name) or 'none'}, want {f.item[name] or 'none'}"
            for name in f.wrong
        ]
    return lines if lines[-1] != "Wrong" else [*lines, "  none"]


def _planted(found: list[Found]) -> list[str]:
    before, after, shown = 0, 0, []
    for f in found:
        if f.got is None:
            shown.append(f"  {f.item['id']}  error: {f.error}")
            continue
        hit = [d for d in f.item["planted"] if reached(d, f.raw_intent or "")]
        left = [d for d in f.item["planted"] if reached(d, f.got.intent or "")]
        before, after = before + len(hit), after + len(left)
        if hit:
            kept = "kept" if left else "dropped"
            shown.append(f"  {f.item['id']}  {kept}: {', '.join(hit)}: {f.raw_intent!r}")
    dropped = sum(f.got is not None and f.got.intent is None for f in found)
    return [
        "Planted details",
        f"  planted: {sum(len(f.item['planted']) for f in found)} in {len(found)} conversations",
        f"  in intents before the filter: {before}",
        f"  in intents after the filter: {after}",
        f"  intents the filter dropped: {dropped}",
        "",
        "Planted details that reached an intent (made up text)",
        *(shown or ["  none"]),
    ]


def report(labelled: list[Found], planted: list[Found], model: Model) -> str:
    price_in, price_cached, price_out = PRICES[DEPLOYMENT]
    fresh = model.input_tokens - model.cached_tokens
    usd = (fresh * price_in + model.cached_tokens * price_cached + model.output_tokens * price_out) / 1_000_000
    count = len(labelled) + len(planted)
    each = f", {1000 * usd / count:.2f} USD per 1,000 conversations" if count else ""
    when = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    dropped = sum(f.got is not None and f.got.intent is None for f in labelled)
    return "\n".join([
        f"Labels of made up conversations, {DEPLOYMENT}, {when}",
        "",
        *_accuracy(labelled),
        "",
        f"Intents the filter dropped in the labelled ones: {dropped}",
        "",
        *_planted(planted),
        "",
        "Cost, with the list prices in scripts/model_costs.py",
        f"  tokens in {model.input_tokens:,} (cached {model.cached_tokens:,}), out {model.output_tokens:,}",
        f"  {usd:.4f} USD{each}",
        "",
    ])


async def both(model: Model, data: dict[str, list[dict]]) -> tuple[list[Found], list[Found], Model]:
    return await check(model, data["labelled"]), await check(model, data["planted"]), model


async def _live(data: dict[str, list[dict]]) -> tuple[list[Found], list[Found], Model]:
    from azure.identity import AzureCliCredential, get_bearer_token_provider

    endpoint = os.environ.get("FOUNDRY_OPENAI_ENDPOINT") or azd_value("FOUNDRY_OPENAI_ENDPOINT")
    token = get_bearer_token_provider(AzureCliCredential(), TOKEN_SCOPE)
    async with httpx.AsyncClient() as http:
        return await both(Model(http, endpoint, token), data)


def ask_model(data: dict[str, list[dict]]) -> tuple[list[Found], list[Found], Model]:
    return asyncio.run(_live(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="save what is printed")
    args = parser.parse_args(argv)
    hush()
    text = report(*ask_model(load()))
    print(text)
    if args.out:
        write_evidence(args.out, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
