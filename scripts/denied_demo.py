# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["voltrieve-api", "voltrieve-agent-core", "httpx"]
#
# [tool.uv.sources]
# voltrieve-api = { path = "../api" }
# voltrieve-agent-core = { path = "../agent_core" }
# ///
"""Runbook step 9, the denied case: a user with only the Chat.User role asks for a test drive.

    uv run scripts/denied_demo.py [--backend loop|prompt] [--client web|car]

With --client the user asks to order a car instead, from the website (denied for the role) or from
the car (denied for the client, which comes first).

The tester account that holds only Chat.User cannot sign in through az, so this runs the real
API in this process with a fake token validator that returns the roles {Chat.User}, against the
real Foundry backend. Expect an authorization_denied (for the car: client_not_allowed) trace event
and a polite refusal, and no call to the dealer service.

Needs `azd env select` and `az login` (the signed-in user needs access to the Foundry project;
nothing else is called). Settings come from the azd environment; no token is sent anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voltrieve_client import azd_value, booking_request, parse_sse  # noqa: E402

AZD_SETTINGS = ("FOUNDRY_PROJECT_ENDPOINT", "CHAT_DEPLOYMENT", "SEARCH_ENDPOINT", "DEALER_URL",
                "DEALER_CLIENT_ID")
# Made up app registrations of the website and the car.
CLIENT_IDS = {"web": "00000000-0000-0000-0000-0000000000e1", "car": "00000000-0000-0000-0000-0000000000e2"}


def question(client: str | None) -> str:
    return "I want to order a Fjord." if client else booking_request()


def expected_step(client: str | None) -> str:
    return "client_not_allowed" if client == "car" else "authorization_denied"


async def run(backend: str, client_name: str | None = None, registry=None) -> int:
    """`registry` takes the place of the real backends, for the tests."""
    import httpx
    from app.auth import Claims
    from app.main import create_app
    from app.settings import Settings

    class ChatUserOnly:
        async def validate(self, authorization):
            return Claims("00000000-0000-0000-0000-00000000dead", "chat.user@example.invalid",
                          ["Chat.User"], azp=CLIENT_IDS.get(client_name, ""))

    settings = Settings(agent_backend=backend, log_level="WARNING", web_client_id=CLIENT_IDS["web"],
                        car_client_id=CLIENT_IDS["car"])
    app = create_app(settings, validator=ChatUserOnly(), registry=registry)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo",
                                     timeout=180.0) as client:
            response = await client.post("/chat", json={"message": question(client_name)},
                                         headers={"X-Voltrieve-Test": "1"})
            response.raise_for_status()
            events = list(parse_sse(response.text.splitlines()))
    finally:
        await app.state.registry.close()

    for kind, data in events:
        if kind == "trace":
            print(f"trace  {data.get('step'):<24} {data.get('detail', '')}")
        elif kind == "error":
            print(f"error  {data}")
    answer = "".join(d.get("text", "") for k, d in events if k == "delta")
    print("\nanswer:", " ".join(answer.split()))
    step = expected_step(client_name)
    denied = any(k == "trace" and d.get("step") == step for k, d in events)
    print(f"\nPASS: {step} emitted" if denied else f"\nFAIL: no {step} event")
    return 0 if denied else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--backend", default="loop", choices=["loop", "prompt"])
    parser.add_argument("--client", choices=sorted(CLIENT_IDS))
    args = parser.parse_args()
    os.environ.setdefault("TENANT_ID", "00000000-0000-0000-0000-0000000000aa")
    os.environ.setdefault("API_CLIENT_ID", "00000000-0000-0000-0000-0000000000cc")
    for name in AZD_SETTINGS:
        os.environ.setdefault(name, azd_value(name))
    return asyncio.run(run(args.backend, args.client))


if __name__ == "__main__":
    sys.exit(main())
