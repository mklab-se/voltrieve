import json
import logging
import time
from typing import Annotated

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver.resolve import Elicit, Resolve
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from voltrieve_doors import Door, DoorConfig, Doors, DoorTool, Gate, RateLimit, Refused, caller, running
from voltrieve_doors.token import DoorTokenValidator

TENANT = "11111111-1111-1111-1111-111111111111"
HOST = "api.example.test"
APPS = {"app-1": "Claude"}

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "k1", "kty": "RSA"}

EXPORTER = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(provider)


def token(door="operations", key=KEY, kid="k1", **changes) -> str:
    now = int(time.time())
    payload = {
        "aud": f"client-{door}", "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "tid": TENANT,
        "exp": now + 600, "nbf": now - 10, "oid": "alice", "azp": "app-1", "scp": f"{door}.read",
        "roles": ["View"], "name": "Alice MARKERNAME",
    }
    payload.update(changes)
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, key, "RS256", headers={"kid": kid})


class Yes(BaseModel):
    ok: bool


def ask(times: int):
    return Elicit(f"Do it {times} times?", Yes)


class World:
    def __init__(self, **options):
        self.calls, self.ran = [], []

        async def health(period: str = "24h") -> dict:
            self.ran.append("health")
            return {"by": caller().oid, "app": caller().app}

        async def secret_tool() -> str:
            self.ran.append("secret_tool")
            return "secret"

        async def breaks(text: str) -> str:
            raise RuntimeError(f"inner {text}")

        async def refuses(text: str) -> str:
            raise Refused("Not today.")

        async def big() -> str:
            return "x" * 100_000

        async def careful(times: int, answer: Annotated[ElicitationResult[Yes], Resolve(ask)]) -> str:
            if not isinstance(answer, AcceptedElicitation) or not answer.data.ok:
                raise Refused("Nothing was done.")
            self.ran.append("careful")
            return "done"

        async def slow() -> str:
            import anyio

            await anyio.sleep(0.3)
            return "slow"

        def tool(fn, **kw):
            base = {"roles": frozenset({"View"}), "scope": "operations.read"}
            return DoorTool(fn.__name__, fn.__name__, "A tool.", fn, **{**base, **kw})

        tools = [tool(health), tool(secret_tool, roles=frozenset({"Admin"})), tool(breaks), tool(refuses), tool(big),
                 tool(careful, people_only=True, read_only=False), tool(slow)]
        gate = options.pop("gate", None) or Gate()
        rate = options.pop("rate", None) or RateLimit(1000)
        self.doors = []
        for name in ("operations", "car"):
            config = DoorConfig(name, f"https://{HOST}/mcp/{name}", TENANT, f"client-{name}", f"https://{HOST}/mcp/{name}",
                                (f"{name}.read",), APPS, applications=options.get("applications", False))
            validator = DoorTokenValidator(TENANT, config.client_id, config.identifier, frozenset(config.scopes), APPS,
                                           applications=config.applications, jwks_fetcher=lambda: {"keys": [JWK]})
            self.doors.append(Door(config, tools, "Instructions.", gate=gate, rate=rate, validator=validator,
                                   on_call=self.calls.append))

        async def rest(scope, receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        self.app = Doors(rest, self.doors)

    def http(self, bearer=None, host=HOST, headers=None):
        all_headers = {**({"authorization": f"Bearer {bearer}"} if bearer else {}), **(headers or {})}
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url=f"https://{host}", headers=all_headers)


@pytest.fixture
async def world():
    # Started and stopped in one task, as a lifespan is.
    w = World()
    import asyncio

    started, stop = asyncio.Event(), asyncio.Event()

    async def life():
        async with running(w.doors):
            started.set()
            await stop.wait()

    task = asyncio.create_task(life())
    await started.wait()
    EXPORTER.clear()
    yield w
    stop.set()
    await task


@pytest.fixture(autouse=True)
def all_logs(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog
