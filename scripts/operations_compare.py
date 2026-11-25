# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["httpx", "httpx2", "mcp==2.3.0", "pyyaml", "pillow"]
# ///
"""A plain model answers questions about operations, with our door and with Microsoft's Azure MCP Server.

    uv run scripts/operations_compare.py [--model gpt-6.1-terra] [--out docs/evidence/operations-door.txt]

The same model gets the same questions twice: once with the tools of the operations door, and once with Azure
MCP Server (npx @azure/mcp, only its monitor tools, read only), which acts with your own Azure rights and is
told the name of the workspace. Two questions. A question stops after 100,000 tokens, so a run costs
at most about 4 SEK (an earlier run without the limit used 390,000 tokens on one question). Ids are masked.

Needs MCP_ALLOW_CLI and Dashboard.View on the door, `az login` with rights to read the workspace and to use the
model, and Node.js.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters
from mcp import types as t

sys.path.insert(0, str(Path(__file__).resolve().parent))

from customer_compare import FOUNDRY_SCOPE  # noqa: E402
from doors_check import Door, token  # noqa: E402
from voltrieve_client import _az_token, azd_value, write_evidence  # noqa: E402

AZURE_MCP = "@azure/mcp@3.0.0"
QUESTIONS = (
    "For the last 7 days, per backend of the Voltrieve assistant: how many turns, how many errors, and the median "
    "time to the first text in milliseconds? Give a table.",
    "Which was the slowest turn of the last 7 days, measured by the time to the first text, and where in that turn "
    "did the time go?",
)
MAX_STEPS = 14
MAX_TOKENS = 100_000  # for one question
MAX_OUTPUT = 8_000  # characters of one tool answer that the model gets


def masked(text: str) -> str:
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}|[0-9a-f]{32}", "<id>", text)
    return re.sub(r"(log|rg|appi)-voltrieve-[\w-]+", r"\1-voltrieve-<env>", text)


async def ask(question: str, client: Client, tools: list[dict], instructions: str, model: str, foundry) -> dict:
    items: list[dict] = [{"role": "user", "content": question}]
    tokens, calls, began = 0, [], time.perf_counter()
    for _ in range(MAX_STEPS):
        r = await foundry.post("/openai/v1/responses", json={
            "model": model, "instructions": instructions, "input": items, "tools": tools, "store": False,
            "include": ["reasoning.encrypted_content"]})
        if r.status_code != 200:
            return {"text": f"(no answer: the model call gave {r.status_code})", "tokens": tokens, "calls": calls,
                    "seconds": time.perf_counter() - began}
        response = r.json()
        tokens += (response.get("usage") or {}).get("total_tokens", 0)
        items += response["output"]
        wanted = [i for i in response["output"] if i["type"] == "function_call"]
        if not wanted:
            text = "".join(c.get("text", "") for i in response["output"] if i["type"] == "message" for c in i["content"])
            return {"text": text, "tokens": tokens, "calls": calls, "seconds": time.perf_counter() - began}
        for c in wanted:
            calls.append(c["name"])
            try:
                result = await client.call_tool(c["name"], json.loads(c["arguments"] or "{}"))
                output = "".join(x.text for x in result.content if isinstance(x, t.TextContent))[:MAX_OUTPUT]
            except Exception as exc:
                output = f"The call failed: {type(exc).__name__}"
            items.append({"type": "function_call_output", "call_id": c["call_id"], "output": output})
        if tokens >= MAX_TOKENS:
            break
    return {"text": "(no answer before the limit of tokens or steps)", "tokens": tokens, "calls": calls, "seconds": time.perf_counter() - began}


async def run(name: str, client: Client, instructions: str, model: str, foundry, lines: list[str]) -> None:
    tools = [{"type": "function", "name": x.name, "description": (x.description or "")[:1000], "parameters": x.input_schema,
              "strict": False} for x in (await client.list_tools()).tools]
    for question in QUESTIONS:
        r = await ask(question, client, tools, instructions, model, foundry)
        lines += ["", f"{name}: {len(tools)} tools, {len(r['calls'])} calls, {r['seconds']:.0f} s, {r['tokens']} tokens",
                  f"  calls: {', '.join(r['calls'])}", f"  question: {question}", *(f"  | {x}" for x in r["text"].splitlines())]
        print(f"{name}: {r['seconds']:.0f} s, {r['tokens']} tokens", file=sys.stderr)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    lines = [f"Two questions about operations, {args.model}, with our door and with {AZURE_MCP} (monitor, read only)."]
    group = azd_value("AZURE_RESOURCE_GROUP")
    azure_hint = (f"You answer questions about an Azure system. Its telemetry is in the Log Analytics workspace of resource group "
                  f"{group}, subscription {azd_value('AZURE_SUBSCRIPTION_ID')}. Find the workspace and its tables, and query it with KQL.")
    async with httpx.AsyncClient(base_url=azd_value("FOUNDRY_PROJECT_ENDPOINT").rstrip("/"), timeout=180,
                                 headers={"Authorization": f"Bearer {_az_token(FOUNDRY_SCOPE, 'rights in Foundry')}"}) as foundry:
        async with Door("operations", token("operations")) as door:
            await run("Operations door", door, door.instructions or "", args.model, foundry, lines)
        server = StdioServerParameters(command="npx", args=["-y", AZURE_MCP, "server", "start", "--namespace", "monitor", "--read-only"],
                                       env={**os.environ, "AZURE_MCP_COLLECT_TELEMETRY": "false"})
        async with Client(server, read_timeout_seconds=180) as azure:
            await run("Azure MCP Server", azure, azure_hint, args.model, foundry, lines)
    text = masked("\n".join(lines) + "\n")
    print(text)
    if args.out:
        write_evidence(args.out, text)


if __name__ == "__main__":
    asyncio.run(main())
