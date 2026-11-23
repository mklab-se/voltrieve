import json
import logging

import pytest

from voltrieve_agent.events import AgentEvent, trace
from voltrieve_agent.loop import TurnOutcome

from app import telemetry
from app.insights import pseudonym
from app.backends import BackendRegistry
from app.logging_setup import JsonFormatter
from app.main import create_app
from app.settings import Settings
from app.turns import TurnStore
from tests.conftest import answer, parse_sse


def test_telemetry_is_off_without_connection_string(monkeypatch):
    calls = []
    monkeypatch.setattr("azure.monitor.opentelemetry.configure_azure_monitor", lambda **kw: calls.append(kw))
    assert telemetry.configure_telemetry("") is False
    assert calls == []


def test_telemetry_starts_with_connection_string(monkeypatch):
    calls = []
    monkeypatch.setattr("azure.monitor.opentelemetry.configure_azure_monitor", lambda **kw: calls.append(kw))
    assert telemetry.configure_telemetry("InstrumentationKey=k") is True
    assert calls == [{"connection_string": "InstrumentationKey=k", "enable_live_metrics": True}]


def test_app_starts_with_and_without_connection_string(monkeypatch, validator, backend):
    calls = []
    monkeypatch.setattr("azure.monitor.opentelemetry.configure_azure_monitor", lambda **kw: calls.append(kw))
    registry = BackendRegistry(Settings(), {"loop": lambda s: backend})
    create_app(Settings(applicationinsights_connection_string=""), validator, registry, TurnStore())
    assert calls == []
    create_app(
        Settings(applicationinsights_connection_string="InstrumentationKey=k"),
        validator,
        registry,
        TurnStore(),
    )
    assert len(calls) == 1


async def test_turn_span_carries_ids_and_correlation_id(client, auth, backend, spans):
    backend.script = [answer("Hi")]
    r = await client.post(
        "/chat", headers={**auth, "X-Correlation-Id": "cid-42"}, json={"message": "hello"}
    )
    done = parse_sse(r.text)[-1][1]
    [span] = [s for s in spans.get_finished_spans() if s.name == "chat.turn"]
    attrs = dict(span.attributes)
    assert {k: attrs[k] for k in ("conversation_id", "turn_id", "backend", "correlation_id")} == {
        "conversation_id": done["conversation_id"],
        "turn_id": done["turn_id"],
        "backend": "loop",
        "correlation_id": "cid-42",
    }
    # Users are counted by pseudonym: no object id, no name, no text on the span.
    assert attrs["voltrieve.user"] == pseudonym("oid-anna")
    assert attrs["voltrieve.outcome"] == "answered" and attrs["gen_ai.request.model"] == "gpt-6.1-terra"
    assert attrs["voltrieve.first_text_ms"] >= 0 and attrs["voltrieve.duration_ms"] >= 0
    assert "user_oid" not in attrs and "oid-anna" not in str(attrs) and "anna@" not in str(attrs)
    assert "hello" not in str(attrs) and "Hi" not in str(attrs.values())


def test_payload_is_truncated_and_only_logged_at_debug(caplog):
    log = logging.getLogger("payload-test")
    with caplog.at_level(logging.INFO, logger="payload-test"):
        telemetry.debug_payload(log, "turn input", "x" * 2000)
    assert caplog.records == []
    with caplog.at_level(logging.DEBUG, logger="payload-test"):
        telemetry.debug_payload(log, "turn input", "x" * 2000)
    [record] = caplog.records
    message = json.loads(JsonFormatter().format(record))["message"]
    assert len(message) < 600 and "1500 more characters" in message


def tool_round_trip():
    async def run(ctx, emit):
        await emit(trace("model_called", "model", "iteration 1"))
        await emit(
            trace("server_tool_called", "tools", "check_stock", tool="check_stock",
                  arguments='{"city": "Malmo", "secret": "ARG-VALUE"}')
        )
        await emit(
            trace("server_tool_returned", "tools", "check_stock", tool="check_stock",
                  output_preview="OUTPUT-VALUE")
        )
        await emit(AgentEvent("delta", {"text": "Two in stock."}))
        return TurnOutcome("done", [], {})

    return run


class FormattedLines(logging.Handler):
    """Formats each record as it is logged, while the request's log context is still set."""

    def __init__(self):
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines = []

    def emit(self, record):
        self.lines.append(json.loads(self.format(record)))


async def test_every_hop_of_a_tool_round_trip_is_a_log_line(client, auth, backend):
    backend.script = [tool_round_trip()]
    handler = FormattedLines()
    logging.getLogger("app.turns").addHandler(handler)
    try:
        await client.post("/chat", headers={**auth, "X-Correlation-Id": "cid-7"}, json={"message": "hi"})
    finally:
        logging.getLogger("app.turns").removeHandler(handler)
    lines = [line for line in handler.lines if line["message"] == "trace"]
    steps = [(line["step"], line["component"]) for line in lines]
    assert steps == [
        ("token_validated", "auth"),
        ("backend_selected", "api"),
        ("conversation_created", "api"),
        ("model_called", "model"),
        ("server_tool_called", "tools"),
        ("server_tool_returned", "tools"),
    ]
    called = lines[4]
    assert called["detail"] == "check_stock" and called["elapsed_ms"] >= 0
    assert all(
        line["backend"] == "loop" and line["user"] == pseudonym("oid-anna") and line["correlation_id"] == "cid-7"
        and "user_oid" not in line
        for line in lines
    )
    # Arguments and results stay out of info logs.
    everything = " ".join(json.dumps(line) for line in lines)
    assert "ARG-VALUE" not in everything and "OUTPUT-VALUE" not in everything


async def test_arguments_and_results_are_logged_at_debug_only(client, auth, backend, caplog):
    backend.script = [tool_round_trip()]
    with caplog.at_level(logging.DEBUG, logger="app.turns"):
        await client.post("/chat", headers=auth, json={"message": "hi"})
    text = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    assert "ARG-VALUE" in text and "OUTPUT-VALUE" in text


async def test_every_hop_is_a_span_event(client, auth, backend, spans):
    backend.script = [tool_round_trip()]
    await client.post("/chat", headers=auth, json={"message": "hi"})
    [span] = [s for s in spans.get_finished_spans() if s.name == "chat.turn"]
    events = {e.name: dict(e.attributes) for e in span.events}
    assert {"token_validated", "server_tool_called", "server_tool_returned"} <= set(events)
    assert events["server_tool_called"]["component"] == "tools"
    assert events["server_tool_called"]["detail"] == "check_stock"
    assert "ARG-VALUE" not in json.dumps(events)


def _feed_events(text: str) -> list[dict]:
    return [data for name, data in parse_sse(text) if name == "turn"]


async def test_dashboard_needs_the_dashboard_role(client, auth):
    r = await client.get("/admin/live", params={"once": "true"}, headers=auth)
    assert r.status_code == 403


async def test_dashboard_feed_shows_turns_and_hides_prompts_by_default(client, auth, backend, make_token):
    backend.script = [answer("Hi")]
    await client.post("/chat", headers=auth, json={"message": "Call me on 070-123 45 67 about the Fjord"})
    admin = {"Authorization": f"Bearer {make_token(roles=['Chat.User', 'Dashboard.View'])}"}
    hidden = await client.get("/admin/live", params={"once": "true"}, headers=admin)
    assert hidden.status_code == 200
    [summary] = [data for name, data in parse_sse(hidden.text) if name == "summary"]
    assert summary["active_users"] == 1 and summary["turns"] == 1
    [turn] = _feed_events(hidden.text)
    assert turn["backend"] == "loop" and turn["outcome"] == "answered" and turn["prompt"] is None
    assert turn["user"] == pseudonym("oid-anna") and turn["duration_ms"] >= 0
    shown = await client.get("/admin/live", params={"once": "true", "prompts": "true"}, headers=admin)
    [turn] = _feed_events(shown.text)
    assert turn["prompt"] == "Call me on [phone] about the Fjord"


async def test_turn_telemetry_never_reaches_the_logs_with_the_prompt(client, auth, backend):
    backend.script = [answer("Hi")]
    handler = FormattedLines()
    logging.getLogger("app").addHandler(handler)
    try:
        await client.post("/chat", headers=auth, json={"message": "PROMPT-TEXT-MARKER"})
    finally:
        logging.getLogger("app").removeHandler(handler)
    assert "PROMPT-TEXT-MARKER" not in json.dumps(handler.lines)


async def test_a_client_tool_round_keeps_the_prompt_on_the_live_feed(app_with_feed):
    feed, service_start = app_with_feed
    service_start("t1", "Honk the horn three times")
    service_start("t1", [{"type": "function_call_output", "call_id": "c", "output": "ok"}])
    [entry] = feed.recent(show_prompts=True)
    assert entry["prompt"] == "Honk the horn three times" and entry["kind"] == "message"
    assert feed.summary()["turns"] == 1


async def test_an_expired_turn_never_reaches_the_live_feed(app_with_feed):
    feed, service_start = app_with_feed
    service_start("made-up-turn-id", "", turn_expired=True)
    assert feed.recent(show_prompts=True) == [] and feed.summary()["turns"] == 0


def test_the_api_trusts_forwarded_client_addresses_from_the_ingress():
    """Application Insights derives the map location from the client address, which only the
    Container Apps ingress knows (X-Forwarded-For)."""
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "--forwarded-allow-ips" in dockerfile


@pytest.fixture
def app_with_feed(app):
    """Start jobs straight on the turn service and look at its live feed."""
    from app.auth import Claims
    from app.turns import Job

    service = app.state.turns
    claims = Claims(oid="oid-anna", username="anna@example.com", roles=["Chat.User"])

    def start(turn_id, user_input, turn_expired=False):
        service.start(Job(claims, "loop", "conv_1", turn_id, user_input, turn_expired=turn_expired))

    return service.feed, start


def test_client_address_is_the_first_forwarded_address():
    from app.main import forwarded_client

    assert forwarded_client({"x-forwarded-for": "203.0.113.7, 100.64.0.2"}) == "203.0.113.7"
    assert forwarded_client({"x-forwarded-for": "  "}) is None and forwarded_client({}) is None
