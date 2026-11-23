import { describe, expect, it, vi } from "vitest";
import { MAX_TOOL_ROUNDS, runTurn } from "./turn";
import { chatReducer, initialChatState } from "./turns";
import type { ClientToolCall, ServerEvent } from "./types";

const calls: ClientToolCall[] = [{ call_id: "1", name: "set_theme", arguments: "{}" }];
const asks: ServerEvent = { type: "client_tool_calls", data: { turn_id: "t", conversation_id: "c", calls } };
const done: ServerEvent = { type: "done", data: { conversation_id: "c", turn_id: "t", backend: "loop" } };

async function* stream(...events: ServerEvent[]): AsyncGenerator<ServerEvent> {
  yield* events;
}

async function run(answers: ServerEvent[][]) {
  const seen: ServerEvent[] = [];
  const runTool = vi.fn((_call: ClientToolCall) => "ok");
  const resume = vi.fn(async (_pause: unknown, _results: unknown) => stream(...(answers.shift() ?? [asks])));
  await runTurn(stream(...answers.shift()!), { emit: (e) => seen.push(e), runTool, resume });
  let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "loop", correlationId: "x" });
  for (const event of seen) state = chatReducer(state, { type: "event", event });
  return { seen, runTool, resume, turn: state.turns[0]! };
}

describe("a turn", () => {
  it("runs the tools and goes on with their results", async () => {
    const { runTool, resume, turn } = await run([[asks], [done]]);
    expect(runTool).toHaveBeenCalledTimes(1);
    expect(resume).toHaveBeenCalledWith({ turnId: "t", conversationId: "c", calls }, [{ call_id: "1", output: "ok" }]);
    expect(turn.status).toBe("done");
  });

  it("stops after five rounds of tools, with an error and not still running", async () => {
    const { seen, runTool, resume, turn } = await run([[asks]]);
    expect(MAX_TOOL_ROUNDS).toBe(5);
    expect(runTool).toHaveBeenCalledTimes(5);
    expect(resume).toHaveBeenCalledTimes(5);
    expect(seen.at(-1)).toMatchObject({ type: "error", data: { code: "too_many_tool_calls" } });
    expect(turn.status).toBe("error");
    expect(turn.error).toBeTruthy();
    // The timeline does not say that tools ran which did not.
    expect(turn.entries.filter((e) => e.kind === "trace" && e.step === "client_tool_ran")).toHaveLength(5);
  });

  it("finishes after five rounds when the sixth answer asks for nothing", async () => {
    const { runTool, turn } = await run([[asks], [asks], [asks], [asks], [asks], [done]]);
    expect(runTool).toHaveBeenCalledTimes(5);
    expect(turn.status).toBe("done");
  });

  it("ends with an error when a tool is asked for without saying for which turn", async () => {
    const { runTool, turn } = await run([[{ type: "client_tool_calls", data: { calls } }]]);
    expect(runTool).not.toHaveBeenCalled();
    expect(turn.status).toBe("error");
  });
});
