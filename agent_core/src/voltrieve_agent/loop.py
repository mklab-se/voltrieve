"""The orchestration loop: stream the model, run server tools, pause for client tools.

The loop never emits `done`: hosts do, because only they know the backend. On status
"failed" the loop has already emitted an `error` event. The turn's correlation id (the turn
id when the host gives none) is sent to the dealer service as X-Correlation-Id, so it should
match `^[A-Za-z0-9._-]{1,64}$`; the dealer replaces any other value.

A turn with an attachment is unstored (see `unstored`): no model call of it leaves anything in the
conversation, and only text is recorded when it is done. A turn that fails records nothing.
"""

import asyncio
import logging
import time
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Literal

from voltrieve_agent.curated import FinalAnswer
from voltrieve_agent.events import (
    REVIEWING,
    RETRYING,
    WAITING_FOR_DEVICE,
    WORKING,
    AgentEvent,
    Emit,
    status,
    trace,
)
from voltrieve_agent.instructions import AGENT_INSTRUCTIONS
from voltrieve_agent.model import (
    Completed,
    Failed,
    FunctionCall,
    McpCall,
    McpStarted,
    ModelClient,
    OutputItem,
    Retrying,
    TextDelta,
    merge_usage,
)
from voltrieve_agent.server_tools import KB_STATUSES
from voltrieve_agent.tools import (
    ClientTool,
    ServerTool,
    ToolSet,
    UserContext,
    current_correlation_id,
)
from voltrieve_agent.unstored import Unstored, assistant_item

logger = logging.getLogger(__name__)

FAILURE_MESSAGES = {
    "model_failed": "The model could not complete the answer.",
    "model_incomplete": "The model stopped before finishing the answer.",
    "model_error": "The model call failed.",
    "too_many_iterations": "The agent did not finish within the allowed number of model calls.",
}


@dataclass
class TurnInput:
    user: UserContext
    conversation_id: str
    turn_id: str
    input: str | list[dict]
    client_tools: list[ClientTool] = field(default_factory=list)
    correlation_id: str | None = None
    # The content parts of an attachment, and what an unstored turn kept when it paused. Neither
    # may be logged or traced.
    parts: list[dict] = field(default_factory=list, repr=False)
    kept: dict | None = field(default=None, repr=False)


@dataclass
class TurnOutcome:
    status: Literal["done", "paused", "failed"]
    pending_outputs: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    client_call_ids: list[str] = field(default_factory=list)
    kept: dict | None = field(default=None, repr=False)  # for TurnInput.kept, when paused
    unrecorded: bool = False  # the answer was given but could not be recorded


def _output_item(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


async def _fail(emit: Emit, code: str, usage: dict) -> TurnOutcome:
    await emit(AgentEvent("error", {"code": code, "message": FAILURE_MESSAGES[code]}))
    return TurnOutcome("failed", [], usage)


async def _record(model, turn, unstored, usage, emit, approved="") -> TurnOutcome:
    """End an unstored turn. The user has the answer by now, so a failure is not theirs to see."""
    items = unstored.record_items(approved)
    if items:
        try:
            await model.record(turn.conversation_id, items)
        except Exception as exc:
            logger.error("Could not record an unstored turn: %s", type(exc).__name__)
            await emit(trace("record_failed", "model"))
            return TurnOutcome("done", [], usage, unrecorded=True)
    return TurnOutcome("done", [], usage)


async def _run_server_tool(toolset, call, user, emit, iteration) -> tuple[str, int]:
    await emit(
        trace(
            "server_tool_called",
            "tools",
            call.name,
            tool=call.name,
            arguments=call.arguments,
            iteration=iteration + 1,
        )
    )
    await emit(status(toolset.status_of(call.name)))
    began = time.perf_counter()
    output = await toolset.execute(call.name, call.arguments, user, emit)
    duration_ms = round((time.perf_counter() - began) * 1000)
    await emit(
        trace(
            "server_tool_returned",
            "tools",
            call.name,
            tool=call.name,
            output_preview=output[:500],
            duration_ms=duration_ms,
        )
    )
    return output, duration_ms


async def _run_server_tools(toolset, calls, user, emit, iteration) -> dict[str, str]:
    """Run the server calls of one model response: reads together, then writes one at a time.
    Returns the output per call id."""
    outputs: dict[str, str] = {}
    together = [c for c in calls if toolset.is_concurrent(c.name)]
    alone = [c for c in calls if not toolset.is_concurrent(c.name)]
    if len(together) > 1:
        names = [c.name for c in together]
        await emit(trace("tools_parallel_started", "tools", ", ".join(names), tools=names))
    began = time.perf_counter()
    results = await asyncio.gather(
        *(_run_server_tool(toolset, c, user, emit, iteration) for c in together)
    )
    outputs.update((c.call_id, output) for c, (output, _) in zip(together, results))
    if len(together) > 1:
        wall_ms = round((time.perf_counter() - began) * 1000)
        one_by_one_ms = sum(duration for _, duration in results)
        await emit(
            trace(
                "tools_parallel_finished",
                "tools",
                f"{len(together)} tools in {wall_ms} ms, {one_by_one_ms} ms one at a time",
                wall_ms=wall_ms,
                one_by_one_ms=one_by_one_ms,
            )
        )
    for call in alone:
        outputs[call.call_id], _ = await _run_server_tool(toolset, call, user, emit, iteration)
    return outputs


async def run_turn(
    model: ModelClient,
    server_tools: list[ServerTool],
    turn: TurnInput,
    emit: Emit,
    max_iterations: int = 8,
) -> TurnOutcome:
    token = current_correlation_id.set(turn.correlation_id or turn.turn_id)
    try:
        return await _run(model, server_tools, turn, emit, max_iterations)
    finally:
        current_correlation_id.reset(token)


async def _run(model, server_tools, turn, emit, max_iterations) -> TurnOutcome:
    toolset = ToolSet(server_tools, turn.client_tools)
    definitions = toolset.definitions()
    usage: dict = {}
    unstored = Unstored.begin(turn.input, turn.parts, turn.kept)
    storing = {} if unstored is None else {"store": False}
    next_input: str | list[dict] = turn.input

    for iteration in range(max_iterations):
        await emit(trace("model_called", "model", f"iteration {iteration + 1}"))
        if iteration > 0:
            await emit(status(REVIEWING))
        calls: list[FunctionCall] = []
        output_items: list[dict] = []
        text_deltas = 0
        failed: Failed | None = None
        stream_error = False
        # Only the model stream is guarded: a failing emit is a host problem, not a model one.
        async with aclosing(
            model.stream(
                instructions=AGENT_INSTRUCTIONS,
                conversation=turn.conversation_id,
                input=next_input if unstored is None else list(unstored.items),
                tools=definitions,
                **storing,
            )
        ) as stream:
            items = aiter(stream)
            while True:
                try:
                    item = await anext(items)
                except StopAsyncIteration:
                    break
                except Exception as exc:
                    if unstored is None:
                        logger.exception("Model stream failed")
                    else:  # the error text may quote the input
                        logger.error("Unstored model stream failed: %s", type(exc).__name__)
                    stream_error = True
                    break
                if isinstance(item, TextDelta):
                    text_deltas += 1
                    if unstored is not None:
                        unstored.text += item.text
                    await emit(AgentEvent("delta", {"text": item.text}))
                elif isinstance(item, FunctionCall):
                    calls.append(item)
                elif isinstance(item, OutputItem):
                    output_items.append(item.raw)
                elif isinstance(item, McpStarted):
                    await emit(status(KB_STATUSES.get(item.server, WORKING)))
                elif isinstance(item, Retrying):
                    await emit(status(RETRYING))
                elif isinstance(item, McpCall):
                    await emit(
                        trace(
                            "mcp_tool_called",
                            "model",
                            f"{item.server}: {item.name}",
                            server=item.server,
                            output_preview=item.output_preview,
                            arguments=item.arguments,
                            started_ms=item.started_ms,
                            finished_ms=item.finished_ms,
                        )
                    )
                elif isinstance(item, Completed):
                    usage = merge_usage(usage, item.usage)
                elif isinstance(item, Failed):
                    failed = item
                    break

        if stream_error:
            return await _fail(emit, "model_error", usage)
        if failed is not None:
            message = failed.message if unstored is None else "(not logged)"
            logger.warning("Model reported %s: %s", failed.code, message)
            code = failed.code if failed.code in FAILURE_MESSAGES else "model_error"
            return await _fail(emit, code, usage)

        await emit(
            trace(
                "model_returned",
                "model",
                f"{text_deltas} text deltas, {len(calls)} tool calls",
                text_deltas=text_deltas,
                tool_calls=len(calls),
            )
        )
        if not calls:
            if unstored is not None:
                return await _record(model, turn, unstored, usage, emit)
            return TurnOutcome("done", [], usage)

        outputs_by_call: dict[str, dict] = {}
        client_calls: list[FunctionCall] = []
        server_calls: list[FunctionCall] = []
        for call in calls:
            if not toolset.is_client(call.name):
                server_calls.append(call)
                continue
            error = toolset.validate_client(call.name, call.arguments)
            if error is None:
                client_calls.append(call)
            else:
                # Never forward arguments the client's own schema rejects.
                await emit(trace("client_tool_rejected", "tools", call.name, tool=call.name))
                outputs_by_call[call.call_id] = _output_item(call.call_id, error)

        results = await _run_server_tools(toolset, server_calls, turn.user, emit, iteration)
        if (
            iteration == 0
            and len(calls) == 1
            and isinstance(results.get(calls[0].call_id), FinalAnswer)
        ):
            # The answer is ready, so skip the model call that would only repeat it. It counts
            # only as the first and only choice: alongside other tools, or after them, it goes
            # to the model like any output. The prompt agent has called it on the way to a job
            # question, and a repair question must not end with a phone number.
            answer = str(results[calls[0].call_id])
            if unstored is None:
                try:
                    await model.record(
                        turn.conversation_id,
                        [_output_item(calls[0].call_id, answer), assistant_item(answer)],
                    )
                except Exception:
                    logger.exception("Could not record the final answer")
                    return await _fail(emit, "model_error", usage)
            await emit(trace("final_answer_sent", "tools", calls[0].name, tool=calls[0].name))
            await emit(AgentEvent("delta", {"text": answer}))
            if unstored is not None:
                # The call was not stored, so there is no call to give an output to.
                return await _record(model, turn, unstored, usage, emit, approved=answer)
            return TurnOutcome("done", [], usage)
        for call_id, output in results.items():
            outputs_by_call[call_id] = _output_item(call_id, output)
        # The model reads the outputs in the order it made the calls, whichever finished first.
        outputs = [outputs_by_call[c.call_id] for c in calls if c.call_id in outputs_by_call]
        if unstored is not None:
            unstored.items += [*output_items, *outputs]

        if client_calls:
            for c in client_calls:
                await emit(
                    trace("client_tool_requested", "tools", c.name, tool=c.name, call_id=c.call_id)
                )
            await emit(status(WAITING_FOR_DEVICE))
            await emit(
                AgentEvent(
                    "client_tool_calls",
                    {
                        "turn_id": turn.turn_id,
                        "calls": [
                            {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
                            for c in client_calls
                        ],
                    },
                )
            )
            call_ids = [c.call_id for c in client_calls]
            if unstored is not None:
                # The outputs are in the kept items. The conversation has no call they answer.
                return TurnOutcome("paused", [], usage, call_ids, kept=unstored.to_dict())
            return TurnOutcome("paused", outputs, usage, call_ids)

        if iteration == max_iterations - 1 and unstored is None:
            # Give every function_call in the conversation an output, without offering tools.
            try:
                async with aclosing(
                    model.stream(
                        instructions=AGENT_INSTRUCTIONS,
                        conversation=turn.conversation_id,
                        input=outputs,
                        tools=[],
                    )
                ) as stream:
                    async for item in stream:
                        if isinstance(item, Completed):
                            usage = merge_usage(usage, item.usage)
            except Exception:
                logger.exception("Closing model call failed")
            return await _fail(emit, "too_many_iterations", usage)

        next_input = outputs

    return await _fail(emit, "too_many_iterations", usage)
