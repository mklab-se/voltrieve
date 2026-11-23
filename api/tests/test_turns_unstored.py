"""Paused turns that keep their items, and the debug log that must not show an attachment."""

import logging

from voltrieve_agent.events import AgentEvent
from voltrieve_agent.loop import TurnOutcome

from app.auth import Claims
from app.backends.base import ABANDONED_OUTPUT, TurnContext
from app.turns import Job, PausedTurn
from tests.conftest import answer, parse_sse

PHOTO = {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}
USER_MESSAGE = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi\n(attachment sent)"}]}
OUTPUT_1 = {"type": "function_call_output", "call_id": "c1", "output": "in stock"}
CLAIMS = Claims("oid-anna", "anna@example.com", ["Chat.User"])


def paused(kept=None, **kwargs) -> PausedTurn:
    fields = dict(
        turn_id="t1",
        oid="oid-anna",
        conversation_id="conv_1",
        backend="loop",
        client_tools=[],
        pending_outputs=[],
        expected_call_ids=frozenset({"c9"}),
        kept=kept,
    )
    return PausedTurn(**{**fields, **kwargs})


def kept_items(head):
    return {"items": [{"type": "function_call", "call_id": "c9"}], "head": head, "text": ""}


def test_abandoning_an_unstored_turn_writes_nothing():
    assert paused(kept=kept_items([USER_MESSAGE])).abandon_outputs() == []


def test_abandoning_after_a_photo_in_a_tool_result_closes_the_stored_call():
    pending = [{"type": "function_call_output", "call_id": "s1", "output": "x"}]
    turn = paused(kept=kept_items([OUTPUT_1]), pending_outputs=pending)
    assert turn.abandon_outputs() == [OUTPUT_1]


def test_abandoning_never_writes_a_photo_from_the_head():
    image_output = {"type": "function_call_output", "call_id": "c1", "output": [PHOTO], "extra": PHOTO}
    turn = paused(kept=kept_items([USER_MESSAGE, image_output, "AAAA", None]))
    assert turn.abandon_outputs() == [
        {"type": "function_call_output", "call_id": "c1", "output": "(attachment sent)"}
    ]
    assert "AAAA" not in str(turn.abandon_outputs())
    assert paused(kept={"head": "AAAA"}).abandon_outputs() == []


def test_abandoning_a_stored_turn_is_as_before():
    pending = [{"type": "function_call_output", "call_id": "s1", "output": "x"}]
    closed = {"type": "function_call_output", "call_id": "c9", "output": ABANDONED_OUTPUT}
    assert paused(pending_outputs=pending).abandon_outputs() == [*pending, closed]


def test_a_paused_turn_and_a_job_do_not_show_the_attachment_in_their_repr():
    kept = {"items": [PHOTO], "head": [], "text": ""}
    job = Job(CLAIMS, "loop", None, "t1", "hi", parts=[PHOTO], kept=kept, context={"plate": "XYZ123"})
    ctx = TurnContext(
        user=None, conversation_id="c", turn_id="t", input="hi", parts=[PHOTO], kept=kept,
        context={"plate": "XYZ123"},
    )
    for obj in (job, ctx, paused(kept=kept)):
        assert "AAAA" not in repr(obj) and "XYZ123" not in repr(obj)


async def drain(stream):
    return [chunk async for chunk in stream]


def pause_with_kept(kept):
    async def run(ctx, emit):
        await emit(AgentEvent("client_tool_calls", {"turn_id": ctx.turn_id, "calls": [
            {"call_id": "c9", "name": "read_file", "arguments": "{}"}]}))
        return TurnOutcome("paused", [], {}, ["c9"], kept=kept)

    return run


async def test_kept_items_survive_a_pause_and_reach_the_backend(app, client, auth, backend):
    kept = {"items": [{"type": "function_call", "call_id": "c9"}], "head": [USER_MESSAGE], "text": "One. "}
    backend.script = [pause_with_kept(kept), answer("Two.")]
    first = Job(CLAIMS, "loop", None, "t1", "hi", [], parts=[PHOTO])
    await drain(app.state.turns.start(first))
    assert backend.contexts[0].parts == [PHOTO] and backend.contexts[0].kept is None
    assert app.state.store.get("t1").kept == kept
    r = await client.post("/chat/tool-results", headers=auth, json={
        "conversation_id": "conv_1", "turn_id": "t1", "results": [{"call_id": "c9", "output": "x"}],
    })
    assert parse_sse(r.text)[-1][0] == "done"
    assert backend.contexts[1].kept == kept and backend.contexts[1].parts == []


async def test_an_unstored_pause_that_expires_closes_nothing_in_foundry(app, client, auth, backend, clock):
    kept = kept_items([USER_MESSAGE])
    backend.script = [pause_with_kept(kept)]
    await drain(app.state.turns.start(Job(CLAIMS, "loop", None, "t1", "hi", [], parts=[PHOTO])))
    clock.now += 10_000
    r = await client.post("/chat/tool-results", headers=auth, json={
        "conversation_id": "conv_1", "turn_id": "t1", "results": [{"call_id": "c9", "output": "x"}],
    })
    assert parse_sse(r.text)[-1][1]["code"] == "turn_expired"
    assert backend.abandoned == []


async def test_a_stale_unstored_pause_closes_nothing_in_foundry(app, backend):
    backend.script = [answer()]
    stale = paused(kept=kept_items([USER_MESSAGE]))
    app.state.store.register_conversation("conv_1", "oid-anna")
    await drain(app.state.turns.start(Job(CLAIMS, "loop", "conv_1", "t2", "again", stale=stale)))
    assert backend.abandoned == []


async def test_an_answer_that_could_not_be_recorded_marks_the_conversation_for_repair(app, backend):
    async def unrecorded(ctx, emit):
        return TurnOutcome("done", unrecorded=True)

    backend.script = [unrecorded, answer()]
    await drain(app.state.turns.start(Job(CLAIMS, "loop", None, "t1", "hi", parts=[PHOTO])))
    assert app.state.store.needs_repair("conv_1")
    await drain(app.state.turns.start(Job(CLAIMS, "loop", "conv_1", "t2", "next")))
    assert backend.repaired == ["conv_1"] and not app.state.store.needs_repair("conv_1")


async def test_a_clean_answer_leaves_the_conversation_clean(app, backend):
    backend.script = [answer()]
    await drain(app.state.turns.start(Job(CLAIMS, "loop", None, "t1", "hi", parts=[PHOTO])))
    assert not app.state.store.needs_repair("conv_1")


async def test_debug_log_of_the_turn_input_has_no_parts(app, backend, caplog):
    async def fail(ctx, emit):
        raise RuntimeError(f"boom {ctx!r}")

    backend.script = [fail]
    kept = {"items": [PHOTO], "head": [], "text": ""}
    job = Job(CLAIMS, "loop", None, "t1", "hi", parts=[PHOTO], kept=kept, context={"plate": "XYZ123"})
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(job))
    assert "Turn failed: RuntimeError" in caplog.text
    assert "AAAA" not in caplog.text and "XYZ123" not in caplog.text


async def pause_after_a_photo_in_a_tool_result(app, backend):
    """A turn whose first call was stored and whose tool result held a photo."""
    kept = kept_items([OUTPUT_1])
    backend.script = [pause_with_kept(kept)]
    await drain(app.state.turns.start(Job(CLAIMS, "loop", None, "t1", "hi", [], parts=[PHOTO])))


async def test_a_new_message_closes_the_stored_call_of_an_unstored_pause_with_its_output(app, client, auth, backend):
    await pause_after_a_photo_in_a_tool_result(app, backend)
    backend.script = [answer()]
    r = await client.post("/chat", headers=auth, json={"message": "again", "conversation_id": "conv_1"})
    assert parse_sse(r.text)[-1][0] == "done"
    assert backend.abandoned == [("conv_1", [OUTPUT_1])]


async def test_an_expired_pause_answered_late_closes_the_stored_call_with_its_output(app, client, auth, backend, clock):
    await pause_after_a_photo_in_a_tool_result(app, backend)
    clock.now += 10_000
    r = await client.post("/chat/tool-results", headers=auth, json={
        "conversation_id": "conv_1", "turn_id": "t1", "results": [{"call_id": "c9", "output": "x"}],
    })
    assert parse_sse(r.text)[-1][1]["code"] == "turn_expired"
    assert backend.abandoned == [("conv_1", [OUTPUT_1])]


async def test_an_evicted_unstored_pause_leaves_the_conversation_for_repair(app, client, auth, backend, clock):
    await pause_after_a_photo_in_a_tool_result(app, backend)
    clock.now += 10_000
    assert app.state.store.evict_expired() == 1
    assert app.state.store.needs_repair("conv_1")
    backend.script = [answer()]
    await client.post("/chat", headers=auth, json={"message": "again", "conversation_id": "conv_1"})
    assert backend.repaired == ["conv_1"]


def trace_details(chunks):
    events = parse_sse(b"".join(chunks).decode())
    return [d["detail"] for kind, d in events if kind == "trace" and d["step"] == "turn_abandoned"]


async def test_the_trace_says_dropped_when_nothing_was_closed(app, backend):
    app.state.store.register_conversation("conv_1", "oid-anna")
    backend.script = [answer(), answer()]
    stale = paused(kept=kept_items([USER_MESSAGE]))
    job = Job(CLAIMS, "loop", "conv_1", "t2", "again", stale=stale)
    assert trace_details(await drain(app.state.turns.start(job))) == ["dropped unfinished turn t1"]
    stale = paused(kept=kept_items([OUTPUT_1]))
    job = Job(CLAIMS, "loop", "conv_1", "t3", "again", stale=stale)
    assert trace_details(await drain(app.state.turns.start(job))) == ["closed unfinished turn t1"]
    assert backend.abandoned == [("conv_1", [OUTPUT_1])]


SECRET_TEXT = "is parking allowed here? the question"


def assert_clean(caplog):
    for secret in ("AAAA", "question", "tool output text", "XYZ123"):
        assert secret not in caplog.text


async def test_a_failed_unstored_turn_logs_the_error_type_only(app, backend, caplog):
    async def fail(ctx, emit):
        raise RuntimeError(f"model said no to {SECRET_TEXT} {PHOTO}")

    backend.script = [fail]
    job = Job(CLAIMS, "loop", None, "t1", SECRET_TEXT, parts=[PHOTO])
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(job))
    assert "Turn failed: RuntimeError" in caplog.text and "in fail" in caplog.text  # the frames
    assert_clean(caplog)


async def test_a_failed_stored_turn_still_logs_the_traceback(app, backend, caplog):
    async def fail(ctx, emit):
        raise RuntimeError("detail for the log")

    backend.script = [fail]
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(Job(CLAIMS, "loop", None, "t1", "hi")))
    assert "detail for the log" in caplog.text


async def test_a_failed_repair_of_an_unstored_turn_logs_the_error_type_only(app, backend, caplog):
    app.state.store.register_conversation("conv_1", "oid-anna", dirty=True)
    backend.repair_error = RuntimeError(f"repair failed {SECRET_TEXT}")
    job = Job(CLAIMS, "loop", "conv_1", "t1", SECRET_TEXT, parts=[PHOTO])
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(job))
    assert "Conversation repair failed" in caplog.text and "RuntimeError" in caplog.text
    assert_clean(caplog)


async def test_a_failed_close_of_an_unstored_pause_logs_the_error_type_only(app, backend, caplog):
    backend.abandon_error = RuntimeError("could not add [tool output text] to the conversation")
    app.state.store.register_conversation("conv_1", "oid-anna")
    backend.script = [answer()]
    head = {"type": "function_call_output", "call_id": "c1", "output": "tool output text"}
    stale = paused(kept=kept_items([head]))
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(Job(CLAIMS, "loop", "conv_1", "t2", "again", stale=stale)))
    assert "Could not close" in caplog.text and "RuntimeError" in caplog.text
    assert_clean(caplog)
    assert backend.repaired == ["conv_1"]  # the failed close left it dirty, and the repair ran


async def test_a_failed_close_of_a_stored_pause_still_logs_the_traceback(app, backend, caplog):
    backend.abandon_error = RuntimeError("detail for the log")
    app.state.store.register_conversation("conv_1", "oid-anna")
    backend.script = [answer()]
    with caplog.at_level(logging.DEBUG):
        await drain(app.state.turns.start(Job(CLAIMS, "loop", "conv_1", "t2", "again", stale=paused())))
    assert "detail for the log" in caplog.text
