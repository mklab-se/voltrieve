import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi, type DealerEvent } from "./api";
import { createDealer } from "./state";
import { dealerState, entry, settle } from "./testing/sample";

const auth = { getToken: async () => "tok" };
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function answer(status: number, body?: unknown) {
  const call = vi.fn(async (_url: string, _init?: RequestInit) => new Response(body === undefined ? null : JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", call);
  return call;
}
const failure = (promise: Promise<unknown>) => promise.then(() => undefined, (e) => e as ApiError);
const never = new AbortController().signal;

describe("a call", () => {
  it("goes to the same origin with a token", async () => {
    const call = answer(200, dealerState());
    expect(await createApi(auth).state(never)).toEqual(dealerState());
    expect(call.mock.calls[0][0]).toBe("/staff/state");
    expect(new Headers(call.mock.calls[0][1]!.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("is one for each change", async () => {
    const call = answer(200, {});
    const api = createApi(auth);
    await api.changeStock("spark", "Malmö", -1);
    await api.deliver("VO-1/../x", "Malmö");
    await api.remove("VO-1001");
    await api.reset();
    expect(call.mock.calls.map(([url, init]) => [init!.method, url, init!.body])).toEqual([
      ["POST", "/staff/stock", JSON.stringify({ model: "spark", city: "Malmö", change: -1 })],
      ["POST", "/staff/orders/VO-1%2F..%2Fx/deliver", JSON.stringify({ city: "Malmö" })],
      ["DELETE", "/staff/orders/VO-1001", undefined],
      ["POST", "/staff/reset", undefined],
    ]);
  });

  it("takes an answer without a body", async () => {
    answer(204);
    await createApi(auth).remove("VO-1001");
  });
});

describe("an error", () => {
  const text = async (status: number, body?: unknown) => {
    answer(status, body);
    return (await failure(createApi(auth).deliver("VO-1001", "Malmö")))?.message;
  };

  it("503 has its own text", async () => {
    expect(await text(503, { detail: "other" })).toBe("The dealer system is not available right now.");
  });

  it("409 shows the detail, when it is a text", async () => {
    expect(await text(409, { detail: "There is none in stock in Malmö." })).toBe("There is none in stock in Malmö.");
    expect(await text(409, { detail: [{ msg: "x" }] })).toBe("Somebody changed this first. Look again.");
    expect(await text(409)).toBe("Somebody changed this first. Look again.");
  });

  it("of any other kind has a fixed text", async () => {
    expect(await text(401, { detail: "secret" })).toBe("The sign-in has ended. Sign out, and sign in again.");
    expect(await text(403, { detail: "secret" })).toBe("This account has no access.");
    expect(await text(404, { detail: "secret" })).toBe("The order is no longer there.");
    expect(await text(500, { detail: "secret" })).toBe("The dealer system could not do that.");
  });

  it("of the network, the sign-in or the answer has a fixed text too", async () => {
    vi.stubGlobal("fetch", async () => Promise.reject(new TypeError("Failed to fetch")));
    expect((await failure(createApi(auth).reset()))?.message).toBe("The dealer system did not answer.");
    const out = { getToken: async () => Promise.reject(new Error("x")) };
    expect((await failure(createApi(out).reset()))?.message).toBe("The sign-in could not be renewed. Sign out, and sign in again.");
    answer(200, { models: [] });
    expect((await failure(createApi(auth).state(never)))?.message).toBe("The dealer system could not do that.");
    vi.stubGlobal("fetch", async () => new Response("<!doctype html>"));
    expect((await failure(createApi(auth).state(never)))?.message).toBe("The dealer system could not do that.");
  });

  it("503 keeps what is shown", async () => {
    vi.useFakeTimers();
    const first = dealerState({ log: [entry(1)] });
    let down = false;
    vi.stubGlobal("fetch", async (url: string) => {
      if (url === "/staff/events") return new Response(new ReadableStream());
      return down ? new Response("{}", { status: 503 }) : new Response(JSON.stringify(first));
    });
    const dealer = createDealer(createApi(auth));
    const stop = dealer.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(dealer.view().state).toEqual(first);
    down = true;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(dealer.view().problem).toBe("The dealer system is not available right now.");
    expect(dealer.view().state).toEqual(first);
    stop();
  });
});

describe("the stream", () => {
  const bytes = (text: string) => new TextEncoder().encode(text);

  it("gives the events it knows, and passes over the rest", async () => {
    const wire = `: keepalive\n\nevent: log\ndata: ${JSON.stringify(entry(7))}\n\nevent: changed\ndata: {}\n\nevent: odd\ndata: {}\n\nevent: log\ndata: not json\n\nevent: clear\ndata: {}\n\n`;
    vi.stubGlobal("fetch", async () => new Response(bytes(wire)));
    const got: DealerEvent[] = [];
    for await (const m of createApi(auth).events(never)) got.push(m);
    expect(got).toEqual([{ event: "open" }, { event: "log", entry: entry(7) }, { event: "changed" }, { event: "clear" }]);
  });

  it("is refused with the status", async () => {
    answer(403);
    const e = await failure((async () => void (await createApi(auth).events(never).next()))());
    expect(e).toMatchObject({ status: 403 });
  });

  it("is given up when nothing came for 45 s, where a keepalive is enough", async () => {
    vi.useFakeTimers();
    let send = (_: Uint8Array) => {};
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", async (_: string, init: RequestInit) => {
      signal = init.signal!;
      return new Response(new ReadableStream({
        start(c) {
          send = (b) => c.enqueue(b);
          signal!.addEventListener("abort", () => c.error(signal!.reason));
        },
      }));
    });
    let over = false;
    void (async () => {
      try {
        for await (const _ of createApi(auth).events(never));
      } catch {
        over = true;
      }
    })();
    await vi.advanceTimersByTimeAsync(40_000);
    send(bytes(": keepalive\n\n"));
    await vi.advanceTimersByTimeAsync(40_000);
    expect(over).toBe(false);
    await vi.advanceTimersByTimeAsync(5_000);
    await settle();
    expect(over).toBe(true);
    expect(signal!.aborted).toBe(true);
  });
});
