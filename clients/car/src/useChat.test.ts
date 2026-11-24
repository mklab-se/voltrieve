import { describe, expect, it, vi } from "vitest";
import type { ServerEvent } from "./types";
import { chatReducer, initialChatState, type ChatAction } from "./turns";
import { createSession, MAX_TOOL_ROUNDS, runTurn, type ChatOptions, type TurnInput } from "./useChat";

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

  it.each(["after", "before"])("runs no tools when an error comes %s they are asked for", async (when) => {
    const error: Frame = ["error", { code: "model_error", message: "x" }];
    const { requests, fetchImpl } = api(() => (when === "after" ? sse(calls("honk"), error) : sse(error, calls("honk"))));
    const runTool = vi.fn(async () => ({ output: "ok" }));
    const events = await run({ runTool }, fetchImpl);
    expect(runTool).not.toHaveBeenCalled();
    expect(requests).toHaveLength(1);
    expect(events.map((e) => e.type)).toEqual(["error"]); // and no tool in the timeline
  });

  it("does not say that tools ran which did not", async () => {
    const { fetchImpl } = api(() => sse(calls("honk")));
    const events = await run({}, fetchImpl);
    expect(events.filter((e) => e.type === "client_tool_calls")).toHaveLength(MAX_TOOL_ROUNDS);
  });

  it("calls it the sign-in only when no token could be had", async () => {
    const { fetchImpl } = api(() => sse(DONE));
    const context = () => {
      throw new TypeError("x is undefined");
    };
    expect(await run({ context }, fetchImpl)).toEqual([{ type: "error", data: { code: "client", message: "x is undefined" } }]);
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

describe("a photo that the API has lost", () => {
  const lost = () => new Response('{"detail":{"code":"unknown_attachment"}}', { status: 422 });

  it("is uploaded again once, and the turn goes on", async () => {
    const { requests, fetchImpl } = api(lost, () => sse(delta("Yes."), DONE));
    const renew = vi.fn(async () => ({ ids: ["new"] }));
    const context = vi.fn(() => CONTEXT);
    const events = await run({ attachments: ["old"], renew, context }, fetchImpl);
    expect(renew).toHaveBeenCalledExactlyOnceWith(["old"]);
    expect(requests.map((r) => r.body.attachments)).toEqual([["old"], ["new"]]);
    expect({ ...requests[1]!.body, attachments: 0 }).toEqual({ ...requests[0]!.body, attachments: 0 });
    expect(context).toHaveBeenCalledTimes(1);
    expect(events.map((e) => e.type)).toEqual(["delta", "done"]);
  });

  it("is uploaded again in the results of a tool too", async () => {
    const { requests, fetchImpl } = api(() => sse(calls("get_sign_photo", "honk")), lost, () => sse(DONE));
    const runTool = vi.fn(async (call: { name: string }) =>
      call.name === "honk" ? { output: "Honked 1 time(s)." } : { output: "The photo is attached.", attachments: ["old"] },
    );
    const events = await run({ runTool, renew: async () => ({ ids: ["new"] }) }, fetchImpl);
    expect(runTool).toHaveBeenCalledTimes(2); // the tools do not run again
    expect(requests[2]!.body.results).toEqual([
      { call_id: "get_sign_photo-0", output: "The photo is attached.", attachments: ["new"] },
      { call_id: "honk-1", output: "Honked 1 time(s)." },
    ]);
    expect(events.at(-1)!.type).toBe("done");
  });

  it("is an error the second time", async () => {
    const { requests, fetchImpl } = api(lost);
    const renew = vi.fn(async () => ({ ids: ["new"] }));
    const events = await run({ attachments: ["old"], renew }, fetchImpl);
    expect(renew).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(2);
    expect(events).toMatchObject([{ type: "error", data: { code: "unknown_attachment" } }]);
  });

  it("is an error the second time in the same turn, also in another request", async () => {
    const { fetchImpl } = api(lost, () => sse(calls("get_sign_photo")), lost);
    const renew = vi.fn(async () => ({ ids: ["new"] }));
    const runTool = async () => ({ output: "The photo is attached.", attachments: ["new"] });
    const events = await run({ attachments: ["old"], renew, runTool }, fetchImpl);
    expect(renew).toHaveBeenCalledTimes(1);
    expect(events.at(-1)).toMatchObject({ type: "error", data: { code: "unknown_attachment" } });
  });

  it("is an error when it cannot be uploaded again", async () => {
    const { requests, fetchImpl } = api(lost);
    const events = await run({ attachments: ["old"], renew: async () => ({ message: "The API is busy." }) }, fetchImpl);
    expect(requests).toHaveLength(1);
    expect(events).toEqual([{ type: "error", data: { code: "unknown_attachment", message: "The API is busy." } }]);
  });

  it("is an error without a way to renew it, and any other error is not tried again", async () => {
    expect(await run({ attachments: ["old"] }, api(lost).fetchImpl)).toMatchObject([{ data: { code: "unknown_attachment" } }]);
    const busy = api(() => new Response('{"detail":{"code":"busy"}}', { status: 503 }));
    const renew = vi.fn(async () => ({ ids: ["new"] }));
    expect(await run({ attachments: ["old"], renew }, busy.fetchImpl)).toMatchObject([{ data: { code: "busy" } }]);
    expect(renew).not.toHaveBeenCalled();
    expect(busy.requests).toHaveLength(1);
  });
});

describe("the chat", () => {
  /** A session whose turns end when the test says so. */
  function session(over: Partial<ChatOptions> = {}) {
    const actions: ChatAction[] = [];
    const turns: { input: TurnInput; emit: (e: ServerEvent) => void; end: () => void }[] = [];
    const busy: boolean[] = [];
    let state = initialChatState;
    const { message: _, correlationId: __, ...options } = input();
    const made = createSession(
      () => ({ options: { ...options, ...over }, conversationId: state.conversationId }),
      (action) => {
        actions.push(action);
        state = chatReducer(state, action);
      },
      (value) => busy.push(value),
      (turn, emit) => new Promise<void>((end) => turns.push({ input: turn, emit, end })),
    );
    return { ...made, actions, turns, busy, state: () => state };
  }

  it("takes one message at a time", () => {
    const chat = session();
    chat.send("one");
    chat.send("two");
    expect(chat.turns.map((t) => t.input.message)).toEqual(["one"]);
    expect(chat.state().turns.map((t) => t.userText)).toEqual(["one"]);
  });

  it("is free again when the turn has ended", async () => {
    const chat = session();
    chat.send("one", ["att1"]);
    expect(chat.turns[0]!.input.attachments).toEqual(["att1"]);
    expect(chat.busy).toEqual([true]);
    chat.turns[0]!.end();
    await vi.waitFor(() => expect(chat.busy).toEqual([true, false]));
    chat.send("two");
    expect(chat.turns).toHaveLength(2);
  });

  it("goes on in the conversation", async () => {
    const chat = session();
    chat.send("one");
    chat.turns[0]!.emit({ type: "done", data: { conversation_id: "c1", turn_id: "t1", backend: "loop" } });
    chat.turns[0]!.end();
    await vi.waitFor(() => expect(chat.busy.at(-1)).toBe(false));
    chat.send("two");
    expect(chat.turns[1]!.input.conversationId).toBe("c1");
  });

  it("takes a new message at once after a reset in the middle of a turn", async () => {
    const onStop = vi.fn();
    const chat = session({ onStop });
    chat.send("one");
    chat.reset();
    expect(chat.turns[0]!.input.signal!.aborted).toBe(true);
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(chat.busy.at(-1)).toBe(false);
    expect(chat.state().turns).toEqual([]);
    chat.send("two");
    expect(chat.turns[1]!.input.message).toBe("two");
    chat.turns[0]!.end(); // the old turn ends late, and that says nothing about the new one
    await Promise.resolve();
    await Promise.resolve();
    expect(chat.busy.at(-1)).toBe(true);
  });

  it("has nothing to stop when it is reset between turns", () => {
    const onStop = vi.fn();
    session({ onStop }).reset();
    expect(onStop).not.toHaveBeenCalled();
  });
});
