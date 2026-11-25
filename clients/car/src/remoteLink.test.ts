import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Code, CarState } from "./remote";
import { backoffMs, LOOK_MS, RemoteLink, SILENCE_MS, type LinkStatus } from "./remoteLink";

beforeEach(() => void vi.useFakeTimers());
afterEach(() => void vi.useRealTimers());

const settle = () => vi.advanceTimersByTimeAsync(0);

/** An API that lets the test hold each stream. */
function setup(answer: (n: number) => number | "stream" | "refuse" = () => "stream") {
  const streams: { say: (event: string, data: unknown) => void; raw: (text: string) => void; end: () => void; token: string; aborted: () => boolean }[] = [];
  const posts: { path: string; body: any; token: string }[] = [];
  let opened = 0;
  const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname;
    const token = (init.headers as Record<string, string>).Authorization!;
    if (path !== "/car/link") {
      posts.push({ path, body: JSON.parse(init.body as string), token });
      return new Response(null, { status: 204 });
    }
    opened += 1;
    const how = answer(opened);
    if (how === "refuse") throw new TypeError("no network");
    if (typeof how === "number") return new Response("{}", { status: how });
    const encoder = new TextEncoder();
    let control!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start: (c) => void (control = c) });
    init.signal!.addEventListener("abort", () => {
      try {
        control.error(new DOMException("aborted", "AbortError"));
      } catch {
        /* it has ended */
      }
    });
    const raw = (text: string) => control.enqueue(encoder.encode(text));
    streams.push({
      raw,
      say: (event, data) => raw(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
      end: () => control.close(),
      token,
      aborted: () => init.signal!.aborted,
    });
    return new Response(body, { status: 200 });
  });
  let tokens = 0;
  const getToken = vi.fn(async () => `token-${(tokens += 1)}`);
  let state: CarState = { parked: true, odometer_km: 8214, wipers: "off" };
  const status: LinkStatus[] = [];
  const onCommand = vi.fn(async (): Promise<Code> => "done");
  const link = new RemoteLink({
    apiUrl: "http://api/",
    getToken,
    state: () => state,
    onCommand,
    onStatus: (s) => void status.push(s),
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  const states = () => posts.filter((p) => p.path.endsWith("/state")).map((p) => p.body);
  const set = (changes: Partial<CarState>) => void (state = { ...state, ...changes });
  return { link, streams, posts, states, set, fetchImpl, getToken, status, onCommand };
}

async function linked(world = setup()) {
  const running = world.link.run();
  await settle();
  world.streams[0]!.say("link", { id: "L1" });
  await settle();
  return { ...world, running, stream: world.streams[0]! };
}

const command = (id = "c1") => ({ id, name: "honk", arguments: { times: 1 }, app: "Claude" });

describe("the link", () => {
  it("opens with the token, and then tells how the car is", async () => {
    const { stream, posts, status } = await linked();
    expect(stream.token).toBe("Bearer token-1");
    expect(posts).toEqual([{ path: "/car/link/L1/state", body: { parked: true, odometer_km: 8214, wipers: "off" }, token: "Bearer token-1" }]);
    expect(status).toEqual(["connecting", "linked"]);
  });

  it("brings a command to the car, and the answer back", async () => {
    const { stream, onCommand, posts } = await linked();
    stream.say("command", command());
    await settle();
    expect(onCommand).toHaveBeenCalledWith(command());
    expect(posts.at(-1)).toMatchObject({ path: "/car/link/L1/results", body: { id: "c1", ok: true } });
    onCommand.mockResolvedValueOnce("moving");
    stream.say("command", command("c2"));
    await settle();
    expect(posts.at(-1)!.body).toEqual({ id: "c2", ok: false, message: "moving" });
  });

  it("leaves alone what is not a command", async () => {
    const { stream, onCommand } = await linked();
    for (const data of [null, 3, {}, { ...command(), arguments: "{}" }, { ...command(), arguments: [] }, { ...command(), app: 1 }]) stream.say("command", data);
    stream.raw("event: command\ndata: {broken\n\n");
    stream.raw(": keep-alive\n\n");
    stream.say("other", command());
    await settle();
    expect(onCommand).not.toHaveBeenCalled();
    stream.say("command", command());
    await settle();
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it("tells when the car drives or the wipers change, and not for every look or kilometer", async () => {
    const { states, set } = await linked();
    await vi.advanceTimersByTimeAsync(10 * LOOK_MS);
    expect(states()).toHaveLength(1);
    set({ parked: false });
    await vi.advanceTimersByTimeAsync(3 * LOOK_MS);
    set({ odometer_km: 8215 });
    await vi.advanceTimersByTimeAsync(3 * LOOK_MS);
    expect(states()).toHaveLength(2);
    set({ wipers: "fast" });
    await vi.advanceTimersByTimeAsync(LOOK_MS);
    set({ parked: true });
    await vi.advanceTimersByTimeAsync(LOOK_MS);
    expect(states().slice(1)).toEqual([
      { parked: false, odometer_km: 8214, wipers: "off" },
      { parked: false, odometer_km: 8215, wipers: "fast" },
      { parked: true, odometer_km: 8215, wipers: "fast" },
    ]);
  });
});

describe("a link that is lost", () => {
  it("is opened again, with a fresh token and the state once more", async () => {
    const { stream, streams, states, posts, status } = await linked();
    stream.say("end", { reason: "expired" });
    stream.end();
    await vi.advanceTimersByTimeAsync(backoffMs(0));
    expect(streams).toHaveLength(2);
    expect(streams[1]!.token).toBe("Bearer token-2");
    streams[1]!.say("link", { id: "L2" });
    await settle();
    expect(states()).toHaveLength(2);
    expect(posts.at(-1)).toMatchObject({ path: "/car/link/L2/state", token: "Bearer token-2" });
    expect(status).toEqual(["connecting", "linked", "connecting", "linked"]);
  });

  it("waits longer each time, up to half a minute", async () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 16_000, 30_000, 30_000]);
    const { link, fetchImpl } = setup((n) => (["refuse", 500, 401] as const)[n % 3]!);
    void link.run();
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    for (let n = 0; n < 5; n += 1) {
      await vi.advanceTimersByTimeAsync(backoffMs(n) - 1);
      expect(fetchImpl).toHaveBeenCalledTimes(n + 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetchImpl).toHaveBeenCalledTimes(n + 2);
    }
    link.close();
  });

  it("does not wait long after one that lasted", async () => {
    const { stream, streams } = await linked(setup((n) => (n === 1 ? "stream" : n < 4 ? 500 : "stream")));
    stream.raw(": keep-alive\n\n");
    await vi.advanceTimersByTimeAsync(20_000);
    stream.end();
    await vi.advanceTimersByTimeAsync(backoffMs(0) + backoffMs(0) + backoffMs(1));
    expect(streams).toHaveLength(2);
  });

  it("is also one on which nothing is heard", async () => {
    const { stream, streams } = await linked();
    await vi.advanceTimersByTimeAsync(SILENCE_MS - 1000);
    stream.raw(": keep-alive\n\n");
    await vi.advanceTimersByTimeAsync(SILENCE_MS - 1000);
    expect(stream.aborted()).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(stream.aborted()).toBe(true);
    await vi.advanceTimersByTimeAsync(backoffMs(0));
    expect(streams).toHaveLength(2);
  });

  it("is tried again when there was no token", async () => {
    const world = setup();
    world.getToken.mockRejectedValueOnce(new Error("no token"));
    void world.link.run();
    await settle();
    expect(world.fetchImpl).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(backoffMs(0));
    expect(world.streams).toHaveLength(1);
    world.link.close();
  });

  it.each([403, 404])("is given up when the API says %i", async (code) => {
    const { link, fetchImpl, status } = setup(() => code);
    await link.run();
    expect(status).toEqual(["connecting", "unavailable"]);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("is given up when another window took its place, so that the two do not fight", async () => {
    const { stream, running, status, streams } = await linked();
    stream.say("end", { reason: "replaced" });
    await running;
    expect(status.at(-1)).toBe("replaced");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(streams).toHaveLength(1);
  });
});

describe("when remote control is switched off", () => {
  it("the stream is closed, and nothing more is done or said", async () => {
    const { link, stream, running, streams, posts, status, set, onCommand } = await linked();
    const said = posts.length;
    link.close();
    await running;
    expect(stream.aborted()).toBe(true);
    set({ parked: false });
    await vi.advanceTimersByTimeAsync(120_000);
    expect([streams.length, posts.length, onCommand.mock.calls.length]).toEqual([1, said, 0]);
    expect(status).toEqual(["connecting", "linked"]);
  });

  it("while it waits or signs in, no link is opened any more", async () => {
    const waiting = setup(() => 500);
    const first = waiting.link.run();
    await settle();
    waiting.link.close();
    await first;
    expect(waiting.fetchImpl).toHaveBeenCalledTimes(1);

    const signing = setup();
    const second = signing.link.run();
    signing.link.close();
    await second;
    expect(signing.fetchImpl).not.toHaveBeenCalled();
  });
});
