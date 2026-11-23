import { describe, expect, it, vi } from "vitest";
import type { ServerEvent } from "./types";
import { MAX_TOOL_ROUNDS, runTurn, type TurnInput } from "./useChat";

type Frame = [event: string, data: unknown];

const DONE: Frame = ["done", { conversation_id: "c1", turn_id: "t1", backend: "loop" }];
const delta = (text: string): Frame => ["delta", { text }];
const calls = (...names: string[]): Frame => [
  "client_tool_calls",
  {
    turn_id: "t1",
    conversation_id: "c1",
    calls: names.map((name, i) => ({ call_id: `${name}-${i}`, name, arguments: "{}" })),
  },
];

function sse(...frames: Frame[]): Response {
  return new Response(frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(""));
}

/** An API that gives each answer in turn, and the last one for ever. */
function api(...answers: (() => Response)[]) {
  const requests: { path: string; body: any; headers: Record<string, string> }[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    requests.push({
      path: new URL(url).pathname,
      body: JSON.parse(init.body as string),
      headers: init.headers as Record<string, string>,
    });
    return answers[Math.min(requests.length, answers.length) - 1]!();
  }) as unknown as typeof fetch;
  return { requests, fetchImpl };
}

const TOOL = { name: "honk", description: "Honk", parameters: { type: "object", properties: {} } };
const CONTEXT = { car: { model: "Voltrieve Spark" }, speed_kmh: 47 };

function input(over: Partial<TurnInput> = {}): TurnInput {
  return {
    auth: { getToken: async () => "tok" },
    config: { apiUrl: "http://api" },
    backend: "loop",
    tools: [TOOL],
    runTool: async () => ({ output: "ok" }),
    context: () => CONTEXT,
    message: "Can I park here?",
    correlationId: "corr-1",
    ...over,
  };
}

async function run(over: Partial<TurnInput>, fetchImpl: typeof fetch): Promise<ServerEvent[]> {
  const events: ServerEvent[] = [];
  await runTurn(input(over), (e) => events.push(e), fetchImpl);
  return events;
}

describe("runTurn", () => {
  it("sends the results of two tool rounds with their attachments", async () => {
    const { requests, fetchImpl } = api(
      () => sse(calls("set_speed", "get_sign_photo")),
      () => sse(calls("park")),
      () => sse(delta("Parked."), DONE),
    );
    const runTool = vi.fn(async (call: { name: string }) =>
      call.name === "get_sign_photo" ? { output: "The photo is attached.", attachments: ["att1"] } : { output: `${call.name} ran` },
    );
    const events = await run({ runTool, conversationId: "c1" }, fetchImpl);

    expect(requests.map((r) => r.path)).toEqual(["/chat", "/chat/tool-results", "/chat/tool-results"]);
    expect(requests[1]!.body).toEqual({
      conversation_id: "c1",
      turn_id: "t1",
      results: [
        { call_id: "set_speed-0", output: "set_speed ran" },
        { call_id: "get_sign_photo-1", output: "The photo is attached.", attachments: ["att1"] },
      ],
    });
    expect(requests[2]!.body.results).toEqual([{ call_id: "park-0", output: "park ran" }]);
    expect(new Set(requests.map((r) => r.headers["X-Correlation-Id"]))).toEqual(new Set(["corr-1"]));
    expect(events.map((e) => e.type)).toEqual(["client_tool_calls", "client_tool_calls", "delta", "done"]);
  });

  it("runs the tools of a round one after the other, in order", async () => {
    const { fetchImpl } = api(() => sse(calls("set_speed", "park")), () => sse(DONE));
    const log: string[] = [];
    const runTool = async (call: { name: string }) => {
      log.push(`${call.name} starts`);
      await new Promise((r) => setTimeout(r, 1));
      log.push(`${call.name} ends`);
      return { output: "ok" };
    };
    await run({ runTool }, fetchImpl);
    expect(log).toEqual(["set_speed starts", "set_speed ends", "park starts", "park ends"]);
  });

  it("runs at most five rounds, and says so", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("honk")));
    const runTool = vi.fn(async () => ({ output: "ok" }));
    const events = await run({ runTool }, fetchImpl);
    expect(MAX_TOOL_ROUNDS).toBe(5);
    expect(runTool).toHaveBeenCalledTimes(5);
    expect(requests).toHaveLength(6); // the message, and five times results
    expect(events.at(-1)).toMatchObject({ type: "error", data: { code: "too_many_tool_calls" } });
  });

  it("reads the context once per message and sends it with the tools and the attachments", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("honk")), () => sse(calls("honk")), () => sse(DONE));
    const context = vi.fn(() => CONTEXT);
    await run({ context, attachments: ["att1"] }, fetchImpl);
    expect(context).toHaveBeenCalledTimes(1);
    expect(requests[0]!.body).toEqual({
      message: "Can I park here?",
      client_tools: [TOOL],
      context: CONTEXT,
      attachments: ["att1"],
    });
    expect(requests[1]!.body).not.toHaveProperty("context");
    expect(requests[1]!.headers["X-Agent-Backend"]).toBe("loop");
  });

  it("names an attachment once in a round, and at most three", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("a", "b", "c", "d", "e")), () => sse(DONE));
    const ids: Record<string, string[]> = { a: ["1"], b: ["1"], c: ["2", "3"], d: ["4"], e: [] };
    await run({ runTool: async (call) => ({ output: "ok", attachments: ids[call.name] }) }, fetchImpl);
    const sent = requests[1]!.body.results.map((r: { attachments?: string[] }) => r.attachments);
    expect(sent).toEqual([["1"], undefined, ["2", "3"], undefined, undefined]);
  });

  it("gives a tool that fails as text, so that the turn goes on", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("honk")), () => sse(DONE));
    const runTool = async () => {
      throw new Error("no speaker");
    };
    const events = await run({ runTool }, fetchImpl);
    expect(requests[1]!.body.results[0].output).toBe("Error: no speaker");
    expect(events.at(-1)!.type).toBe("done");
  });

  it("ends with an error when no token can be had", async () => {
    const { requests, fetchImpl } = api(() => sse(DONE));
    const getToken = async () => {
      throw new Error("Not signed in.");
    };
    const events = await run({ auth: { getToken } }, fetchImpl);
    expect(requests).toEqual([]);
    expect(events).toEqual([{ type: "error", data: { code: "auth", message: "Not signed in." } }]);
  });

  it("stops after an abort: no more events, no more requests", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("honk")), () => sse(DONE));
    const ctrl = new AbortController();
    const runTool = async () => {
      ctrl.abort();
      return { output: "ok" };
    };
    const events = await run({ runTool, signal: ctrl.signal }, fetchImpl);
    expect(requests).toHaveLength(1);
    expect(events.map((e) => e.type)).toEqual(["client_tool_calls"]);
  });

  it("ends with an error when the API asks for a tool in no turn", async () => {
    const { fetchImpl } = api(() => sse(["client_tool_calls", { calls: [] }]));
    const events = await run({}, fetchImpl);
    expect(events.at(-1)).toMatchObject({ type: "error", data: { code: "malformed_event" } });
  });
});
