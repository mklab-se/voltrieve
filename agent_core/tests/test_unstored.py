"""Turns with attachments: the model calls are not stored, and only text is recorded."""

import asyncio
import json
import logging
import traceback
import warnings
from types import SimpleNamespace

import openai
import pytest
from openai.types.responses import ResponseFunctionToolCallItem

from voltrieve_agent.curated import CURATED_ANSWERS
from voltrieve_agent.loop import TurnInput, run_turn
from voltrieve_agent.model import (
    Completed,
    Failed,
    FunctionCall,
    McpCall,
    OutputItem,
    ResponsesModelClient,
    TextDelta,
)
from voltrieve_agent.server_tools import CURATED_TOOL, build_server_tools
from voltrieve_agent.tools import ClientTool, ServerTool
from voltrieve_agent.unstored import Unstored

IMG = {"type": "input_image", "detail": "high", "image_url": "data:image/jpeg;base64,AAAA"}
ANY_OBJECT = {"type": "object", "properties": {}}
CLIENT_TOOL = ClientTool("take_photo", "Take a photo", ANY_OBJECT)


class FakeModel:
    """Yields one scripted list of stream items per call, and keeps the calls and the recordings."""

    def __init__(self, scripts, record_error=None):
        self.scripts = list(scripts)
        self.calls = []
        self.recorded = []
        self.record_error = record_error

    async def stream(self, **kwargs):
        # A copy through JSON: what was sent at the time, and it must be possible to send.
        self.calls.append(json.loads(json.dumps(kwargs)))
        for item in self.scripts.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item

    async def record(self, conversation, items):
        if self.record_error:
            raise self.record_error
        self.recorded.append(items)


def call_items(call_id, name="check_stock", arguments="{}"):
    """What the client yields for one function call in an unstored call."""
    raw = {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
    return [FunctionCall(call_id, name, arguments), OutputItem(raw)]


def output(call_id, text="ok"):
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def user_message(*content):
    return {"type": "message", "role": "user", "content": list(content)}


def assistant(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def text_part(text):
    return {"type": "input_text", "text": text}


def stock_tool(result="in stock", name="check_stock"):
    async def handler(args, user):
        return result

    return ServerTool(name, "d", ANY_OBJECT, handler)


def photo_turn(user, client_tools=()):
    return TurnInput(user, "c", "t", "Can I park?", list(client_tools), parts=[IMG])


def steps(events):
    return [e.data["step"] for e in events.events if e.type == "trace"]


def types(events):
    return [e.type for e in events.events]


SENT = {
    "type": "message",
    "role": "user",
    "content": [{"type": "input_text", "text": "Can I park?\n(attachment sent)"}],
}


async def test_turn_with_a_photo_is_unstored_and_records_text_only(user, events):
    model = FakeModel([[TextDelta("No parking"), Completed({})]])
    out = await run_turn(model, [], TurnInput(user, "c", "t", "Can I park?", parts=[IMG]), events)
    assert out.status == "done" and model.calls[0]["store"] is False
    assert model.calls[0]["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Can I park?"}, IMG]}
    ]
    user_item, answer = model.recorded[0]
    assert user_item["content"] == [{"type": "input_text", "text": "Can I park?\n(attachment sent)"}]
    assert answer["content"][0]["text"] == "No parking" and "AAAA" not in json.dumps(model.recorded)
    assert user_item == SENT and answer == assistant("No parking")
    assert model.calls[0]["conversation"] == "c"
    assert out.kept is None and out.unrecorded is False


async def test_items_are_carried_between_calls(user, events):
    model = FakeModel(
        [
            [
                OutputItem({"type": "reasoning", "encrypted_content": "xyz"}),
                McpCall("search", "kb", "hit"),
                OutputItem({"type": "mcp_call", "name": "search"}),
                *call_items("c1"),
                Completed({"total_tokens": 3}),
            ],
            [TextDelta("Yes"), OutputItem({"type": "message"}), Completed({"total_tokens": 4})],
        ]
    )
    out = await run_turn(model, [stock_tool()], photo_turn(user), events)
    assert [i["type"] for i in model.calls[1]["input"]] == [
        "message",
        "reasoning",
        "mcp_call",
        "function_call",
        "function_call_output",
    ]
    assert model.calls[1]["input"][0] == user_message(text_part("Can I park?"), IMG)
    assert model.calls[1]["input"][-1] == output("c1", "in stock")
    assert all(c["store"] is False for c in model.calls)
    assert out.usage == {"total_tokens": 7}
    assert model.recorded == [[SENT, assistant("Yes")]]


async def test_outputs_follow_the_items_in_the_order_of_the_calls(user, events):
    async def slow(args, u):
        await asyncio.sleep(0.01)
        return "slow"

    tools = [ServerTool("slow", "d", ANY_OBJECT, slow), stock_tool()]
    rejecting = ClientTool("noise", "d", {"type": "object", "required": ["times"]})
    model = FakeModel(
        [
            [*call_items("c1", "slow"), *call_items("c2", "noise"), *call_items("c3")],
            [*call_items("c4")],
            [TextDelta("ok")],
        ]
    )
    await run_turn(model, tools, photo_turn(user, [rejecting]), events)
    ids = [(i["type"], i["call_id"]) for i in model.calls[2]["input"][1:]]
    assert ids == [
        *[("function_call", c) for c in ("c1", "c2", "c3")],
        *[("function_call_output", c) for c in ("c1", "c2", "c3")],
        ("function_call", "c4"),
        ("function_call_output", "c4"),
    ]


async def test_photo_in_a_tool_result_records_the_outputs_and_the_answer(user, events):
    result = output("call_1", "photo taken")
    model = FakeModel([[TextDelta("No parking"), Completed({})]])
    turn = TurnInput(user, "c", "t", [result], parts=[IMG])
    out = await run_turn(model, [], turn, events)
    assert out.status == "done" and model.calls[0]["store"] is False
    assert [i["type"] for i in model.calls[0]["input"]] == ["function_call_output", "message"]
    assert model.calls[0]["input"] == [result, user_message(IMG)]
    assert model.recorded[0][0] == result and model.recorded[0][1]["role"] == "assistant"
    assert model.recorded == [[result, assistant("No parking")]]


async def test_pause_keeps_the_items_and_writes_nothing(user, events):
    model = FakeModel([[TextDelta("One. "), *call_items("c1", "take_photo"), Completed({"total_tokens": 2})]])
    out = await run_turn(model, [], photo_turn(user, [CLIENT_TOOL]), events)
    assert out.status == "paused" and out.pending_outputs == [] and model.recorded == []
    assert out.kept["items"][-1]["type"] == "function_call"
    assert out.kept["head"] == [SENT] and out.kept["text"] == "One. "
    assert out.client_call_ids == ["c1"] and out.usage == {"total_tokens": 2}


async def test_pause_keeps_the_server_outputs_in_the_items(user, events):
    model = FakeModel([[*call_items("c1"), *call_items("c2", "take_photo")]])
    out = await run_turn(model, [stock_tool()], photo_turn(user, [CLIENT_TOOL]), events)
    assert out.pending_outputs == [] and model.recorded == []
    assert out.kept["items"][-1] == output("c1", "in stock")
    assert out.client_call_ids == ["c2"]


async def test_a_kept_turn_continues_and_records_the_whole_answer(user, events):
    first = FakeModel([[TextDelta("One. "), *call_items("c1", "take_photo")]])
    paused = await run_turn(first, [], photo_turn(user, [CLIENT_TOOL]), events)
    # The hosted backend sends what is kept over HTTP.
    kept = json.loads(json.dumps(paused.kept))
    before = json.dumps(kept)
    model = FakeModel([[TextDelta("Two."), Completed({})]])
    turn = TurnInput(user, "c", "t", [output("c1", "photo taken")], [CLIENT_TOOL], kept=kept)
    out = await run_turn(model, [], turn, events)
    assert out.status == "done" and model.calls[0]["store"] is False
    assert model.calls[0]["input"] == [*kept["items"], output("c1", "photo taken")]
    assert model.recorded == [[SENT, assistant("One. Two.")]]
    assert model.recorded[0][-1]["content"][0]["text"] == "One. Two."
    assert json.dumps(kept) == before


async def test_a_kept_turn_takes_new_parts_and_pauses_again(user, events):
    """Decision 4: kept items, the outputs, then one user message with the new parts."""
    other = {**IMG, "image_url": "data:image/jpeg;base64,BBBB"}
    kept = Unstored([user_message(text_part("Can I park?"), IMG)], [SENT], "One. ").to_dict()
    model = FakeModel([[TextDelta("Two. "), *call_items("c2", "take_photo")]])
    turn = TurnInput(
        user, "c", "t", [output("c1"), output("c9")], [CLIENT_TOOL], parts=[other], kept=kept
    )
    out = await run_turn(model, [], turn, events)
    sent = [*kept["items"], output("c1"), output("c9"), user_message(other)]
    assert model.calls[0]["input"] == sent
    assert out.status == "paused"
    assert out.kept["items"][: len(sent)] == sent and out.kept["items"][-1]["call_id"] == "c2"
    assert out.kept["head"] == [SENT] and out.kept["text"] == "One. Two. "
    assert model.recorded == []


async def test_a_photo_in_a_tool_result_then_a_pause_keeps_the_first_outputs_as_head(user, events):
    result = output("call_1", "photo taken")
    model = FakeModel([[*call_items("c2", "take_photo")], [TextDelta("Yes")]])
    turn = TurnInput(user, "c", "t", [result], [CLIENT_TOOL], parts=[IMG])
    paused = await run_turn(model, [], turn, events)
    turn = TurnInput(user, "c", "t", [output("c2")], [CLIENT_TOOL], kept=paused.kept)
    await run_turn(model, [], turn, events)
    # Only the call that is open in the conversation gets its output there.
    assert model.recorded == [[result, assistant("Yes")]]


@pytest.mark.parametrize(
    "script", [[TextDelta("No"), Failed("model_failed", "boom")], [TextDelta("No"), RuntimeError("x")]]
)
async def test_failed_unstored_turn_records_nothing(user, events, script):
    for input_ in ("Can I park?", [output("call_1")]):
        model = FakeModel([script])
        out = await run_turn(model, [], TurnInput(user, "c", "t", input_, parts=[IMG]), events)
        assert out.status == "failed" and model.recorded == []
        assert out.kept is None and out.unrecorded is False


async def test_the_last_iteration_fails_without_the_closing_call(user, events):
    model = FakeModel([call_items("c1"), call_items("c2")])
    out = await run_turn(model, [stock_tool()], photo_turn(user), events, max_iterations=2)
    assert out.status == "failed" and len(model.calls) == 2 and model.recorded == []
    error = next(e for e in events.events if e.type == "error")
    assert error.data["code"] == "too_many_iterations"


def curated_script():
    return [*call_items("c1", CURATED_TOOL, json.dumps({"topic": "contact"})), Completed({})]


async def test_curated_answer_in_an_unstored_turn(user, events):
    model = FakeModel([curated_script()])
    out = await run_turn(model, build_server_tools(None, None), photo_turn(user), events)
    approved = CURATED_ANSWERS["contact"].text
    assert out.status == "done" and len(model.calls) == 1
    assert [i["type"] for i in model.recorded[0]] == ["message", "message"]
    assert model.recorded == [[SENT, assistant(approved)]]
    assert [e.data["text"] for e in events.events if e.type == "delta"] == [approved]
    assert "final_answer_sent" in steps(events)


async def test_curated_answer_after_a_photo_in_a_tool_result(user, events):
    model = FakeModel([curated_script()])
    turn = TurnInput(user, "c", "t", [output("call_1")], parts=[IMG])
    await run_turn(model, build_server_tools(None, None), turn, events)
    assert model.recorded == [[output("call_1"), assistant(CURATED_ANSWERS["contact"].text)]]


async def test_text_before_a_curated_answer_is_recorded_as_its_own_message(user, events):
    kept = Unstored([user_message(IMG)], [SENT], "One. ").to_dict()
    model = FakeModel([curated_script()])
    turn = TurnInput(user, "c", "t", [output("c0")], kept=kept)
    await run_turn(model, build_server_tools(None, None), turn, events)
    approved = CURATED_ANSWERS["contact"].text
    assert model.recorded == [[SENT, assistant("One. "), assistant(approved)]]


async def test_failed_recording_is_logged_and_the_turn_is_done(user, events, caplog):
    model = FakeModel([[TextDelta("No parking")]], record_error=RuntimeError("No parking AAAA"))
    out = await run_turn(model, [], photo_turn(user), events)
    assert out.status == "done" and out.unrecorded is True
    assert "record_failed" in steps(events) and "error" not in types(events)
    assert "RuntimeError" in caplog.text
    assert "No parking" not in caplog.text and "AAAA" not in caplog.text


async def test_failed_recording_of_a_curated_answer_still_sends_it(user, events):
    model = FakeModel([curated_script()], record_error=RuntimeError("down"))
    out = await run_turn(model, build_server_tools(None, None), photo_turn(user), events)
    assert out.status == "done" and out.unrecorded is True
    assert [e.data["text"] for e in events.events if e.type == "delta"] == [
        CURATED_ANSWERS["contact"].text
    ]
    assert "record_failed" in steps(events) and "error" not in types(events)


async def test_a_failing_emit_after_a_failed_recording_does_not_carry_the_items(user, caplog):
    """Hosts log what escapes with its traceback, and a chained error prints its message too."""
    caplog.set_level(logging.DEBUG)

    class Echoing(FakeModel):
        async def record(self, conversation, items):
            raise RuntimeError(f"rejected: {json.dumps(items)}")

    async def emit(event):
        if event.data.get("step") == "record_failed":
            raise ConnectionError("closed")

    model = Echoing([[TextDelta("No parking")]])
    with pytest.raises(ConnectionError) as raised:
        await run_turn(model, [], photo_turn(user), emit)
    shown = "".join(traceback.format_exception(raised.value))
    logging.getLogger("test").error("Turn failed", exc_info=raised.value)
    for text in (shown, caplog.text):
        assert "ConnectionError" in text
        assert "No parking" not in text and "Can I park?" not in text
        assert "rejected: " not in text


@pytest.mark.parametrize("content", [[IMG], [text_part("photo taken"), IMG], None, 7, {"a": IMG}])
async def test_an_output_that_is_not_text_is_not_recorded(user, events, content):
    """The last guard before the conversation, which is stored."""
    result = output("call_1", content)
    model = FakeModel([[TextDelta("No parking")]])
    turn = TurnInput(user, "c", "t", [result, output("call_2", "ok")], parts=[IMG])
    out = await run_turn(model, [], turn, events)
    assert out.status == "done" and out.unrecorded is False
    assert "AAAA" not in json.dumps(model.recorded)
    assert model.recorded == [
        [output("call_1", "(attachment sent)"), output("call_2", "ok"), assistant("No parking")]
    ]
    assert model.calls[0]["input"][0] == result


@pytest.mark.parametrize("content", [[IMG], [text_part("look"), IMG], [text_part([IMG])], None, 7])
def test_a_kept_head_is_checked_before_it_is_recorded(content):
    kept = {"items": [], "head": [user_message()], "text": "Yes"}
    kept["head"][0]["content"] = content
    items = Unstored.from_dict(kept).record_items()
    assert items == [user_message(text_part("(attachment sent)")), assistant("Yes")]


def test_only_outputs_and_user_text_are_recorded_from_a_kept_head():
    text = {"type": "message", "role": "user", "content": "plain"}
    kept = {
        "items": [],
        "head": [IMG, "AAAA", None, text, SENT, {**output("c1"), "extra": IMG}],
        "text": "Yes",
    }
    items = Unstored.from_dict(kept).record_items()
    assert items == [text, SENT, output("c1"), assistant("Yes")]


async def test_a_kept_turn_continued_by_text_sends_it_as_one_message(user, events):
    kept = Unstored([user_message(IMG)], [SENT], "One. ").to_dict()
    for parts, content in (([], [text_part("hello")]), ([IMG], [text_part("hello"), IMG])):
        model = FakeModel([[TextDelta("Two.")]])
        await run_turn(model, [], TurnInput(user, "c", "t", "hello", parts=parts, kept=kept), events)
        assert model.calls[0]["input"] == [user_message(IMG), user_message(*content)]


async def test_an_empty_answer_records_only_outputs(user, events):
    """Decision 8: an open call in the conversation gets its output, and no message is empty."""
    model = FakeModel([[Completed({})]])
    out = await run_turn(model, [], photo_turn(user), events)
    assert out.status == "done" and model.recorded == [] and out.unrecorded is False
    model = FakeModel([[Completed({})]])
    turn = TurnInput(user, "c", "t", [output("call_1")], parts=[IMG])
    out = await run_turn(model, [], turn, events)
    assert out.status == "done" and model.recorded == [[output("call_1")]]


async def test_the_answer_has_the_text_of_every_call(user, events):
    """Decision 8: text before a server tool is part of the answer too."""
    model = FakeModel([[TextDelta("One. "), *call_items("c1")], [TextDelta("Two.")]])
    await run_turn(model, [stock_tool()], photo_turn(user), events)
    assert model.recorded == [[SENT, assistant("One. Two.")]]


async def test_turn_without_parts_is_as_before(user, events):
    model = FakeModel([[TextDelta("Hi"), Completed({})]])
    out = await run_turn(model, [], TurnInput(user, "c", "t", "hello"), events)
    assert "store" not in model.calls[0] and model.calls[0]["input"] == "hello" and model.recorded == []
    assert out.status == "done" and out.kept is None and out.unrecorded is False
    assert steps(events) == ["model_called", "model_returned"]


async def test_pause_without_parts_is_as_before(user, events):
    model = FakeModel([[FunctionCall("c1", "check_stock", "{}"), FunctionCall("c2", "take_photo", "{}")]])
    turn = TurnInput(user, "c", "t", "hello", [CLIENT_TOOL])
    out = await run_turn(model, [stock_tool()], turn, events)
    assert out.pending_outputs == [output("c1", "in stock")] and out.kept is None


def test_kept_survives_json():
    """Decision 13: the hosted backend sends it over HTTP."""
    kept = Unstored([user_message(text_part("Får jag parkera?"), IMG)], [SENT], "One. ")
    again = Unstored.from_dict(json.loads(json.dumps(kept.to_dict())))
    assert again == kept
    assert kept.to_dict() == {"items": kept.items, "head": kept.head, "text": "One. "}


def test_from_dict_and_to_dict_do_not_share_their_lists():
    source = Unstored([], [], "").to_dict()
    kept = Unstored.from_dict(source)
    given = kept.to_dict()
    kept.items.append(output("c1"))
    kept.head.append(SENT)
    assert source == given == {"items": [], "head": [], "text": ""}


async def test_no_photo_in_logs_or_events(user, events, caplog):
    caplog.set_level(logging.DEBUG)
    leak = "bad request: data:image/jpeg;base64,AAAA"
    tools = [stock_tool(), *build_server_tools(None, None)]
    scripts = [
        [[TextDelta("One. "), *call_items("c1")], [TextDelta("Two.")]],
        [[*call_items("c1", "take_photo")]],
        [curated_script()],
        [[Failed("model_error", leak)]],
        [[RuntimeError(leak)]],
        [call_items("c1")] * 8,
    ]
    outcomes = []
    for script in scripts:
        for error in (None, RuntimeError(leak)):
            model = FakeModel(script, record_error=error)
            turn = photo_turn(user, [CLIENT_TOOL])
            outcomes.append(await run_turn(model, tools, turn, events))
            logging.getLogger("test").debug("%r %r", turn, outcomes[-1])
            if outcomes[-1].kept:
                logging.getLogger("test").debug("%r", Unstored.from_dict(outcomes[-1].kept))
    assert {o.status for o in outcomes} == {"done", "paused", "failed"}
    assert caplog.records
    assert "AAAA" not in caplog.text
    assert "AAAA" not in json.dumps([[e.type, e.data] for e in events.events])


# The Responses API client.


class Raw:
    """A finished output item as the SDK gives it."""

    def __init__(self, **fields):
        self.fields = fields
        self.dumped_with = None
        self.__dict__.update(fields)

    def model_dump(self, **kwargs):
        self.dumped_with = kwargs
        return {k: v for k, v in self.fields.items() if v is not None}


def done(index, **fields):
    return SimpleNamespace(type="response.output_item.done", output_index=index, item=Raw(**fields))


class Stream:
    def __init__(self, events, error=None):
        self.events, self.error = events, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for event in self.events:
            yield event
        if self.error:
            raise self.error


class FakeOpenAI:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.requests = []
        self.recorded = []
        self.responses = SimpleNamespace(create=self._create)
        self.conversations = SimpleNamespace(items=SimpleNamespace(create=self._record))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.streams.pop(0)

    async def _record(self, conversation, *, items):
        self.recorded.append((conversation, items))


async def collect(client, **kwargs):
    base = dict(instructions="i", conversation="c", input="hi", tools=[])
    return [item async for item in client.stream(**base, **kwargs)]


@pytest.mark.parametrize("target", [{"model": "m"}, {"agent_name": "a"}])
async def test_unstored_request(target):
    api = FakeOpenAI(Stream([]), Stream([]), Stream([]))
    client = ResponsesModelClient(api, **target)
    await collect(client, store=False)
    await collect(client)
    await collect(client, store=True)
    unstored, stored, explicit = api.requests
    assert unstored["store"] is False and unstored["include"] == ["reasoning.encrypted_content"]
    assert "store" not in stored and "include" not in stored
    assert {k: v for k, v in unstored.items() if k not in ("store", "include")} == stored == explicit


async def test_output_items_are_yielded_only_when_unstored():
    def stream():
        return Stream(
            [
                done(0, type="reasoning", id="r1", encrypted_content="xyz", status=None),
                done(1, type="function_call", call_id="c1", name="f", arguments="{}"),
                SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None)),
            ]
        )

    first = stream()
    api = FakeOpenAI(first, stream())
    client = ResponsesModelClient(api, "m")
    items = await collect(client, store=False)
    assert [i.raw for i in items if isinstance(i, OutputItem)] == [
        {"type": "reasoning", "id": "r1", "encrypted_content": "xyz"},
        {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"},
    ]
    assert first.events[0].item.dumped_with == {"exclude_none": True, "warnings": False}
    assert FunctionCall("c1", "f", "{}") in items and Completed({}) in items
    assert await collect(client) == [FunctionCall("c1", "f", "{}"), Completed({})]


async def test_output_items_give_no_pydantic_warning():
    """Such a warning prints the value of the field."""
    item = ResponseFunctionToolCallItem.construct(
        type="function_call",
        id="f1",
        call_id="c1",
        name="f",
        arguments="{}",
        created_by={"secret": "AAAA"},
    )
    event = SimpleNamespace(type="response.output_item.done", output_index=0, item=item)
    with warnings.catch_warnings(record=True) as control:
        warnings.simplefilter("always")
        item.model_dump(exclude_none=True)
    assert "AAAA" in str(control[0].message)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        items = await collect(ResponsesModelClient(FakeOpenAI(Stream([event])), "m"), store=False)
    assert caught == []
    assert items[-1].raw["call_id"] == "c1"


async def test_output_items_come_in_the_order_of_the_response():
    """Searches run together and can finish in any order. They are sent back as the model made them."""
    api = FakeOpenAI(
        Stream(
            [
                done(2, type="function_call", call_id="c1", name="f", arguments="{}"),
                done(1, type="mcp_call", name="b", server_label="kb", output="x", arguments=""),
                done(0, type="reasoning"),
            ]
        )
    )
    items = await collect(ResponsesModelClient(api, "m"), store=False)
    assert [i.raw["type"] for i in items if isinstance(i, OutputItem)] == [
        "reasoning",
        "mcp_call",
        "function_call",
    ]


async def test_an_unstored_call_is_retried_unstored_and_drops_the_first_items():
    limited = openai.APIError("Model deployment rate limit exceeded.", request=None, body=None)
    api = FakeOpenAI(
        Stream([done(0, type="reasoning", id="r1")], limited),
        Stream([done(0, type="reasoning", id="r2")]),
    )

    async def sleep(seconds):
        pass

    items = await collect(ResponsesModelClient(api, "m", sleep=sleep), store=False)
    assert [i.raw["id"] for i in items if isinstance(i, OutputItem)] == ["r2"]
    assert [r["store"] for r in api.requests] == [False, False]


@pytest.mark.parametrize("count, sizes", [(1, [1]), (20, [20]), (21, [20, 1]), (45, [20, 20, 5])])
async def test_record_splits_into_requests_of_at_most_twenty_items(count, sizes):
    """Decision 10."""
    api = FakeOpenAI()
    items = [output(f"c{n}") for n in range(count)]
    await ResponsesModelClient(api, "m").record("conv", items)
    assert [len(sent) for _, sent in api.recorded] == sizes
    assert [i for _, sent in api.recorded for i in sent] == items
    assert {conversation for conversation, _ in api.recorded} == {"conv"}
