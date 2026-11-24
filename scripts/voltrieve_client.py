"""A small client for the Voltrieve chat contract, shared by the runbook and the grounding check.

Standard library plus httpx. Talks to the deployed API with an Entra token from the az CLI.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

Event = tuple[str, dict]

# Tells the API that a run is ours, so that it is never counted as use.
TEST_HEADER = {"X-Voltrieve-Test": "1"}
# With the value "car" the API treats the run as the car's. It has no other effect.
CLIENT_HEADER = "X-Voltrieve-Client"


def azd_value(name: str) -> str:
    result = subprocess.run(
        ["azd", "env", "get-value", name], capture_output=True, text=True, check=False
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise SystemExit(f"Could not read {name} from the azd environment. Run `azd env select` first.")
    return value


def api_url() -> str:
    return (os.environ.get("VOLTRIEVE_API_URL") or azd_value("API_URL")).rstrip("/")


def api_token(scope_app_id: str | None = None) -> str:
    app_id = scope_app_id or os.environ.get("VOLTRIEVE_API_CLIENT_ID") or azd_value("API_CLIENT_ID")
    result = subprocess.run(
        ["az", "account", "get-access-token", "--scope", f"api://{app_id}/access_as_user",
         "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=False,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise SystemExit("Could not get a token from the az CLI. Run `az login` as a user with Chat.User.")
    return token


def parse_sse(lines: Iterable[str]) -> Iterator[Event]:
    name, data = "message", []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if data:
                yield name, _decode("\n".join(data))
            name, data = "message", []
        elif line.startswith(":"):
            continue
        else:
            field_name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field_name == "event":
                name = value
            elif field_name == "data":
                data.append(value)
    if data:
        yield name, _decode("\n".join(data))


def _decode(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"value": value}


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


@dataclass
class Turn:
    events: list[Event] = field(default_factory=list)
    conversation_id: str | None = None
    tool_runs: list[tuple[str, str, str]] = field(default_factory=list)  # name, arguments, output
    first_text_seconds: float | None = None
    first_status_seconds: float | None = None

    @property
    def text(self) -> str:
        return "".join(d.get("text", "") for t, d in self.events if t == "delta").strip()

    @property
    def error(self) -> dict | None:
        return next((d for t, d in self.events if t == "error"), None)

    @property
    def done(self) -> dict | None:
        return next((d for t, d in reversed(self.events) if t == "done"), None)

    @property
    def trace_steps(self) -> list[str]:
        return [d.get("step", "") for t, d in self.events if t == "trace"]

    def trace(self, step: str) -> list[dict]:
        return [d for t, d in self.events if t == "trace" and d.get("step") == step]

    @property
    def event_types(self) -> list[str]:
        return [t for t, _ in self.events]


# A tool handler gives back text, or a dict with `output` and `attachments` (ids of uploads).
ToolHandler = Callable[[str], "str | dict"]


@dataclass
class PhotoTurn:
    """A turn with a photo: the upload and, when the photo was accepted, the turn."""

    upload: dict
    turn: Turn | None = None
    upload_seconds: float = 0.0
    turn_seconds: float = 0.0  # the turn alone, the upload not included
    reuploaded: bool = False


class Api:
    def __init__(
        self, base_url: str, token: Callable[[], str], backend: str, timeout: float = 180.0, client: str = ""
    ):
        self._token = token
        self._backend = backend
        self._client = client
        self._http = httpx.Client(base_url=base_url, timeout=timeout, headers=TEST_HEADER)

    def close(self) -> None:
        self._http.close()

    def upload(self, data: bytes, label: str = "") -> dict:
        """Upload an attachment and return the answer: `ready` with an id, or `rejected`."""
        headers = {"Authorization": f"Bearer {self._token()}", **TEST_HEADER}
        try:
            response = self._http.post("/attachments", files={"file": data}, data={"label": label}, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(0, f"/attachments failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text[:300])
        return response.json()

    def turn_with_photo(self, message: str, data: bytes, label: str = "photo", **turn_args) -> PhotoTurn:
        """Upload a photo and ask about it. A photo the server has lost (it scaled to zero between
        the upload and the turn) is uploaded again, once: that is not a wrong answer. A rejected
        photo gives no turn."""
        began = time.perf_counter()
        upload = self.upload(data, label)
        result = PhotoTurn(upload, upload_seconds=round(time.perf_counter() - began, 2))
        while upload.get("status") == "ready":
            began = time.perf_counter()
            try:
                result.turn = self.turn(message, attachments=[upload["id"]], **turn_args)
                result.turn_seconds = round(time.perf_counter() - began, 2)
                return result
            except ApiError as exc:
                if result.reuploaded or "unknown_attachment" not in exc.detail:
                    raise
            upload = result.upload = self.upload(data, label)
            result.reuploaded = True
        return result

    def _stream(self, path: str, body: dict, correlation_id: str | None) -> Iterator[Event]:
        headers = {"Authorization": f"Bearer {self._token()}", "X-Agent-Backend": self._backend,
                   **TEST_HEADER}
        if self._client:
            headers[CLIENT_HEADER] = self._client
        if correlation_id:
            headers["X-Correlation-Id"] = correlation_id
        try:
            with self._http.stream("POST", path, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    response.read()
                    raise ApiError(response.status_code, response.text[:300])
                yield from parse_sse(response.iter_lines())
        except httpx.HTTPError as exc:
            raise ApiError(0, f"{path} failed: {type(exc).__name__}") from exc

    def turn(
        self,
        message: str,
        conversation_id: str | None = None,
        client_tools: list[dict] | None = None,
        handlers: dict[str, ToolHandler] | None = None,
        correlation_id: str | None = None,
        max_rounds: int = 6,
        attachments: list[str] | None = None,
        context: dict | None = None,
    ) -> Turn:
        """Send a message and follow the turn to its end, answering client tool calls."""
        result = Turn(conversation_id=conversation_id)
        began = time.perf_counter()
        body: dict = {"message": message, "client_tools": client_tools or []}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if attachments:
            body["attachments"] = attachments
        if context:
            body["context"] = context
        stream = self._stream("/chat", body, correlation_id)
        for _ in range(max_rounds):
            pending = None
            for name, data in stream:
                result.events.append((name, data))
                if name == "status" and result.first_status_seconds is None:
                    result.first_status_seconds = round(time.perf_counter() - began, 2)
                if name == "delta" and result.first_text_seconds is None:
                    result.first_text_seconds = round(time.perf_counter() - began, 2)
                result.conversation_id = data.get("conversation_id") or result.conversation_id
                if name == "client_tool_calls":
                    pending = data
            if pending is None:
                return result
            outputs = []
            for call in pending.get("calls", []):
                handler = (handlers or {}).get(call.get("name", ""))
                arguments = call.get("arguments") or "{}"
                output = handler(arguments) if handler else "error: no such client tool"
                text = output["output"] if isinstance(output, dict) else output
                result.tool_runs.append((call.get("name", ""), arguments, text))
                answer = {"call_id": call.get("call_id", ""), "output": text}
                if isinstance(output, dict) and output.get("attachments"):
                    answer["attachments"] = output["attachments"]
                outputs.append(answer)
            stream = self._stream(
                "/chat/tool-results",
                {"conversation_id": result.conversation_id, "turn_id": pending.get("turn_id"),
                 "results": outputs},
                correlation_id,
            )
        raise ApiError(0, "too many client tool rounds")


def _seconds_left(token: str) -> float:
    """Seconds until a JWT expires; infinity when it carries no readable expiry."""
    try:
        body = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return claims["exp"] - time.time()
    except (IndexError, ValueError, KeyError):
        return float("inf")


def cached_token(fetch: Callable[[], str], ttl: float = 1200.0, margin: float = 600.0) -> Callable[[], str]:
    """Refetch after ttl seconds, or sooner when the token has less than margin seconds left:
    the az CLI hands back its own cached token, which may be close to expiring."""
    state: dict = {"value": None, "at": 0.0}

    def get() -> str:
        stale = state["value"] is None or time.monotonic() - state["at"] > ttl
        if stale or _seconds_left(state["value"]) < margin:
            state["value"], state["at"] = fetch(), time.monotonic()
        return state["value"]

    return get


def evidence_text(text: str) -> str:
    """The text of a file saved under docs/evidence/.

    Model output is otherwise kept verbatim. Two things are changed on the way to disk: an
    em-dash becomes " - " (the repository avoids the character, and models like it), and
    absolute paths are removed so that no file carries the name of the machine's user.
    """
    text = text.replace(str(ROOT) + os.sep, "").replace(str(ROOT), ".")
    text = re.sub(
        r"(?:/Users|/home)/[^\s\"'`),;]+"
        r"|(?<![A-Za-z0-9])[A-Za-z]:\\{1,2}(?:[^\s\\\"'`),;]+\\{1,2})+[^\s\\\"'`),;]*",
        "<path>",
        text,
    )
    return re.sub(r"[ \t]*\u2014[ \t]*", " - ", text)


def write_evidence(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evidence_text(text))
    return path


def booking_request() -> str:
    """The runbook's booking question with the coming Saturday spelled out, because the model
    has no calendar and the dealer wants a future YYYY-MM-DD date."""
    today = dt.date.today()
    saturday = today + dt.timedelta(days=(5 - today.weekday()) % 7 or 7)
    return f"Book me a test drive of the Atlas in G\u00f6teborg on Saturday ({saturday.isoformat()})."
