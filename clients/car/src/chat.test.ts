// Copied from clients/web; keep the two alike.
import { describe, expect, it } from "vitest";
import { buildChatRequest, buildToolResultsRequest, streamChat } from "./chat";
import type { ServerEvent } from "./types";
import { chatReducer, initialChatState } from "./turns";

const TOOL = { name: "honk", description: "Honk", parameters: { type: "object", properties: {} } };
const base = { apiUrl: "https://api.example/", token: "tok", backend: "loop" as const, message: "hello", correlationId: "corr-1", clientTools: [TOOL] };

function sseResponse(chunks: string[], status = 200): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
  });
  return new Response(body, { status, headers: { "Content-Type": "text/event-stream" } });
}

async function collect(gen: AsyncGenerator<ServerEvent>): Promise<ServerEvent[]> {
  const out: ServerEvent[] = [];
  for await (const e of gen) out.push(e);
  return out;
}

describe("buildChatRequest", () => {
  it("sends the message and this client's tools on the first turn", () => {
    const { url, init } = buildChatRequest(base);
    expect(url).toBe("https://api.example/chat");
    const body = JSON.parse(init.body as string);
    expect(body.message).toBe("hello");
    expect(body.client_tools).toEqual([TOOL]);
  });

  it("includes conversation_id on later turns, with the tools again", () => {
    const { init } = buildChatRequest({ ...base, conversationId: "c1" });
    const body = JSON.parse(init.body as string);
    expect(body.conversation_id).toBe("c1");
    expect(body.message).toBe("hello");
    expect(body.client_tools).toEqual([TOOL]);
  });

  it("sends the context and the attachments when there are any", () => {
    const body = (input: typeof base & { context?: { speed_kmh: number }; attachments?: string[] }) =>
      JSON.parse(buildChatRequest(input).init.body as string);
    expect(body({ ...base, context: { speed_kmh: 47 }, attachments: ["a1"] })).toMatchObject({
      context: { speed_kmh: 47 },
      attachments: ["a1"],
    });
    expect(Object.keys(body({ ...base, attachments: [] }))).toEqual(["message", "client_tools"]);
  });

  it("sets bearer auth and the backend header", () => {
    const { init } = buildChatRequest({ ...base, backend: "hosted" });
    const h = init.headers as Record<string, string>;
    expect(h.Authorization).toBe("Bearer tok");
    expect(h["X-Agent-Backend"]).toBe("hosted");
    expect(h["X-Correlation-Id"]).toBe("corr-1");
    expect(init.method).toBe("POST");
  });
});

describe("the tool results request", () => {
  it("continues the paused turn on the same correlation id", () => {
    const results = [{ call_id: "1", output: "ok", attachments: ["a1"] }];
    const { url, init } = buildToolResultsRequest({ ...base, conversationId: "c1", turnId: "t1", results });
    expect(url).toBe("https://api.example/chat/tool-results");
    expect(JSON.parse(init.body as string)).toEqual({ conversation_id: "c1", turn_id: "t1", results });
    expect((init.headers as Record<string, string>)["X-Correlation-Id"]).toBe("corr-1");
  });
});

describe("streamChat", () => {
  it("keeps the code of the API's error", async () => {
    const lost = (async () => new Response('{"detail":{"code":"unknown_attachment"}}', { status: 422 })) as unknown as typeof fetch;
    const events = await collect(streamChat(base, undefined, lost));
    expect(events).toMatchObject([{ type: "error", data: { code: "unknown_attachment" } }]);
  });

  it("yields typed events across split chunks", async () => {
    const fetchImpl = (async () =>
      sseResponse([
        'event: trace\ndata: {"step":"s","component":"api","detail":"d","elapsed_ms":5}\n',
        '\nevent: delta\ndata: {"text":"Hi"}\n\nevent: done\n',
        'data: {"conversation_id":"c","turn_id":"t","backend":"loop","usage":{}}\n\n',
      ])) as unknown as typeof fetch;
    const events = await collect(streamChat(base, undefined, fetchImpl));
    expect(events.map((e) => e.type)).toEqual(["trace", "delta", "done"]);
    expect(events[0]).toMatchObject({ data: { component: "api", elapsed_ms: 5 } });
  });

  it("maps HTTP failures and network failures to error events", async () => {
    const f403 = (async () => new Response("", { status: 403 })) as unknown as typeof fetch;
    const e1 = await collect(streamChat(base, undefined, f403));
    expect(e1).toMatchObject([{ type: "error", data: { code: "http_403" } }]);
    // A 403 is not always a missing role: it is also a missing scope or someone else's conversation.
    const message = (e1[0] as { data: { message: string } }).data.message;
    expect(message).toContain("Chat.User");
    expect(message).toContain("access_as_user");
    expect(message).not.toMatch(/role is required/);

    const fnet = (async () => {
      throw new TypeError("failed");
    }) as unknown as typeof fetch;
    const e2 = await collect(streamChat(base, undefined, fnet));
    expect(e2).toMatchObject([{ type: "error", data: { code: "network" } }]);
  });

  it("yields an error when the stream ends without done or error", async () => {
    const fetchImpl = (async () =>
      sseResponse(['event: delta\ndata: {"text":"Hi"}\n\n'])) as unknown as typeof fetch;
    const events = await collect(streamChat(base, undefined, fetchImpl));
    expect(events.map((e) => e.type)).toEqual(["delta", "error"]);
    expect(events[1]).toMatchObject({
      data: { code: "incomplete", message: "The stream ended before the turn completed." },
    });
  });

  it("decodes a multibyte character split across byte chunks", async () => {
    const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"Göteborg"}\n\nevent: done\ndata: {"conversation_id":"c","turn_id":"t","backend":"loop"}\n\n');
    const split = bytes.indexOf(0xc3) + 1; // between the two bytes of "ö"
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, split));
        c.enqueue(bytes.slice(split));
        c.close();
      },
    });
    const fetchImpl = (async () => new Response(body, { status: 200 })) as unknown as typeof fetch;
    const events = await collect(streamChat(base, undefined, fetchImpl));
    expect(events[0]).toMatchObject({ type: "delta", data: { text: "Göteborg" } });
    expect(events[1]!.type).toBe("done");
  });

  it("reports malformed event JSON as an error event instead of throwing", async () => {
    const fetchImpl = (async () => sseResponse(["event: delta\ndata: {nope\n\n"])) as unknown as typeof fetch;
    const events = await collect(streamChat(base, undefined, fetchImpl));
    expect(events).toMatchObject([{ type: "error", data: { code: "malformed_event" } }]);
  });
});

describe("status events", () => {
  it("shows the latest activity until the answer starts", () => {
    let state = chatReducer(initialChatState, { type: "start", message: "hi", backend: "loop", correlationId: "c" });
    state = chatReducer(state, { type: "event", event: { type: "status", data: { code: "thinking", message: "Thinking" } } });
    state = chatReducer(state, { type: "event", event: { type: "status", data: { code: "searching_parts", message: "Searching the parts catalog" } } });
    expect(state.turns[0]!.activity).toBe("Searching the parts catalog");
  });
});
