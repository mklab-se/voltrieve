import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDealer, deliverOptions, type Dealer } from "./state";
import { dealerState, entry, fakeApi, order, settle } from "./testing/sample";

beforeEach(() => {
  vi.useFakeTimers();
});
let stop = () => {};
afterEach(() => {
  stop();
  vi.useRealTimers();
});

async function started(first = dealerState()) {
  const f = fakeApi();
  const dealer: Dealer = createDealer(f.api);
  stop = dealer.start();
  await settle();
  f.reads[0].resolve(first);
  await settle();
  const numbers = () => dealer.view().arrived.map((o) => o.number);
  /** The stream opens, and the read that comes with it answers. */
  const open = async (state = first) => {
    f.streams.at(-1)!.push({ event: "open" });
    await settle();
    f.reads.at(-1)!.resolve(state);
    await settle();
  };
  const changed = async (state = first) => {
    f.streams.at(-1)!.push({ event: "changed" });
    await settle();
    f.reads.at(-1)!.resolve(state);
    await settle();
  };
  return { ...f, dealer, numbers, open, changed };
}

describe("orders that arrive", () => {
  const [a, b, c] = [order("VO-1001"), order("VO-1002"), order("VO-1003")];

  it("are not the ones that were there", async () => {
    const t = await started(dealerState({ orders: [b, a] }));
    expect(t.numbers()).toEqual([]);
    expect(t.dealer.view().state?.orders).toHaveLength(2);
  });

  it("are the ones that come later, once", async () => {
    const t = await started(dealerState({ orders: [a] }));
    await t.open(dealerState({ orders: [b, a] }));
    expect(t.numbers()).toEqual(["VO-1002"]);
    t.dealer.dismiss("VO-1002");
    expect(t.numbers()).toEqual([]);
    await t.changed(dealerState({ orders: [b, a] }));
    expect(t.numbers()).toEqual([]);
  });

  it("stay until they are dismissed, also when they are delivered meanwhile", async () => {
    const t = await started();
    await t.open(dealerState({ orders: [a] }));
    await t.changed(dealerState({ orders: [{ ...a, status: "delivered", city: "Malmö" }] }));
    expect(t.dealer.view().arrived).toMatchObject([{ number: "VO-1001", status: "delivered" }]);
  });

  it("are not given again after a lost connection, and one from meanwhile is given", async () => {
    const t = await started();
    await t.open(dealerState({ orders: [a] }));
    t.dealer.dismiss("VO-1001");
    t.streams[0].end();
    await settle();
    expect(t.dealer.view().live).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(t.streams).toHaveLength(2);
    await t.open(dealerState({ orders: [b, a] }));
    expect(t.numbers()).toEqual(["VO-1002"]);
  });

  it("keep their order, the oldest first", async () => {
    const t = await started();
    await t.open(dealerState({ orders: [b, a] }));
    expect(t.numbers()).toEqual(["VO-1001", "VO-1002"]);
    await t.changed(dealerState({ orders: [c, b, a] }));
    t.dealer.dismiss("VO-1001");
    expect(t.numbers()).toEqual(["VO-1002", "VO-1003"]);
  });

  it("are gone after a clear with no orders, and so is the log", async () => {
    const t = await started(dealerState({ log: [entry(1)] }));
    await t.open(dealerState({ orders: [a], log: [entry(1)] }));
    t.streams[0].push({ event: "clear" });
    await settle();
    expect(t.dealer.view().state?.log).toEqual([]);
    t.reads.at(-1)!.resolve(dealerState());
    await settle();
    expect(t.numbers()).toEqual([]);
    expect(t.dealer.view().state?.orders).toEqual([]);
  });

  it("do not come from a read that a clear overtook", async () => {
    const t = await started();
    t.streams[0].push({ event: "changed" });
    await settle();
    t.streams[0].push({ event: "clear" });
    await settle();
    t.reads[1].resolve(dealerState({ orders: [a], log: [entry(1)] }));
    await settle();
    expect(t.numbers()).toEqual([]);
    expect(t.dealer.view().state?.log).toEqual([]);
    expect(t.reads).toHaveLength(3);
  });

  it("are counted from the first read that answers", async () => {
    const t = await started();
    stop();
    const f = fakeApi();
    const dealer = createDealer(f.api);
    stop = dealer.start();
    await settle();
    f.reads[0].reject(f.error(503, "The dealer system is not available right now."));
    await settle();
    expect(dealer.view()).toMatchObject({ problem: "The dealer system is not available right now." });
    f.streams[0].push({ event: "open" });
    await settle();
    f.reads[1].resolve(dealerState({ orders: [a] }));
    await settle();
    expect(dealer.view().arrived).toEqual([]);
    expect(dealer.view().problem).toBeUndefined();
    expect(t.streams[0].closed).toBe(true);
  });

  it("survive a page that stops and starts, as React does in development", async () => {
    const t = await started(dealerState({ orders: [a] }));
    stop();
    stop = t.dealer.start();
    await settle();
    t.reads.at(-1)!.resolve(dealerState({ orders: [b, a] }));
    await settle();
    expect(t.numbers()).toEqual(["VO-1002"]);
  });
});

describe("reading", () => {
  it("is one at a time, and one more after it", async () => {
    const t = await started();
    for (let n = 0; n < 3; n++) t.streams[0].push({ event: "changed" });
    await settle();
    expect(t.reads).toHaveLength(2);
    t.reads[1].resolve(dealerState());
    await settle();
    expect(t.reads).toHaveLength(3);
    t.reads[2].resolve(dealerState());
    await settle();
    expect(t.reads).toHaveLength(3);
  });

  it("happens every 60 s", async () => {
    const t = await started();
    await t.open();
    const before = t.reads.length;
    await vi.advanceTimersByTimeAsync(59_000);
    expect(t.reads).toHaveLength(before);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(t.reads).toHaveLength(before + 1);
    t.reads.at(-1)!.resolve(dealerState());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.reads).toHaveLength(before + 2);
  });

  it("that fails keeps what is shown, and tries again", async () => {
    const t = await started(dealerState({ orders: [order("VO-1001")] }));
    t.streams[0].push({ event: "changed" });
    await settle();
    t.reads[1].reject(t.error(503, "The dealer system is not available right now."));
    await settle();
    expect(t.dealer.view().state?.orders).toHaveLength(1);
    expect(t.dealer.view().problem).toBe("The dealer system is not available right now.");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(t.reads).toHaveLength(3);
    t.reads[2].resolve(dealerState());
    await settle();
    expect(t.dealer.view().problem).toBeUndefined();
  });

  it("that works again takes a notice with the same text away, and no other", async () => {
    const t = await started();
    const text = "The dealer system is not available right now.";
    for (const [status, notice, left] of [[503, text, undefined], [409, "There is none.", "There is none."]] as const) {
      void t.dealer.changeStock("spark", "Malmö", -1);
      t.writes.at(-1)!.reject(t.error(status, notice));
      await settle();
      t.reads.at(-1)!.reject(t.error(503, text));
      await settle();
      expect(t.dealer.view()).toMatchObject({ notice, problem: text });
      await vi.advanceTimersByTimeAsync(10_000);
      t.reads.at(-1)!.resolve(dealerState());
      await settle();
      expect(t.dealer.view().notice).toBe(left);
    }
  });

  it("that keeps failing is tried every 10 s, and no more often as time goes", async () => {
    const t = await started();
    await t.open();
    await vi.advanceTimersByTimeAsync(3000); // not in step with the read of each minute
    t.streams[0].push({ event: "changed" });
    await settle();
    let failed = t.reads.length - 1;
    for (let second = 0; second < 300; second++) {
      for (; failed < t.reads.length; failed++) {
        t.reads[failed].reject(t.error(503, "x"));
        await settle();
      }
      await vi.advanceTimersByTimeAsync(1000);
    }
    // Every 10 s, and the one of each minute starts the 10 s over.
    expect(failed).toBeGreaterThan(25);
    expect(failed).toBeLessThanOrEqual(40);
  });

  it("stops with a 403, and so does the stream", async () => {
    const t = await started();
    t.streams[0].push({ event: "changed" });
    await settle();
    t.reads[1].reject(t.error(403, "no"));
    await settle();
    expect(t.dealer.view().denied).toBe(true);
    expect(t.dealer.view().state).toBeUndefined();
    expect(t.streams[0].closed).toBe(true);
    await vi.advanceTimersByTimeAsync(200_000);
    expect(t.reads).toHaveLength(2);
    expect(t.streams).toHaveLength(1);
  });

  it("stops when the page stops", async () => {
    const t = await started();
    await t.open();
    stop();
    await settle();
    expect(t.streams[0].closed).toBe(true);
    expect(t.dealer.view().live).toBe(false);
    const before = t.reads.length;
    await vi.advanceTimersByTimeAsync(200_000);
    expect(t.reads).toHaveLength(before);
    expect(t.streams).toHaveLength(1);
  });
});

describe("the connection", () => {
  it("comes back after 1, 2, 5 and then 10 s, and starts over after an open one", async () => {
    const t = await started();
    for (const [n, seconds] of [1, 2, 5, 10, 10].entries()) {
      t.streams[n].end(new Error("lost"));
      await vi.advanceTimersByTimeAsync(seconds * 1000 - 1);
      expect(t.streams).toHaveLength(n + 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(t.streams).toHaveLength(n + 2);
    }
    await t.open();
    t.streams.at(-1)!.end();
    await vi.advanceTimersByTimeAsync(1000);
    expect(t.streams).toHaveLength(7);
  });

  it("is live while it is open, and lost only after it has been live", async () => {
    const t = await started();
    expect(t.dealer.view()).toMatchObject({ live: false, lost: false });
    t.streams[0].end(new Error("never open"));
    await settle();
    expect(t.dealer.view()).toMatchObject({ live: false, lost: false });
    await vi.advanceTimersByTimeAsync(1000);
    await t.open();
    expect(t.dealer.view()).toMatchObject({ live: true, lost: false });
    t.streams[1].end();
    await settle();
    expect(t.dealer.view()).toMatchObject({ live: false, lost: true });
  });

  it("reads the state each time it opens", async () => {
    const t = await started();
    const before = t.reads.length;
    await t.open();
    expect(t.reads).toHaveLength(before + 1);
  });
});

describe("the log", () => {
  it("grows with what the stream sends, and does not get an entry twice", async () => {
    const t = await started(dealerState({ log: [entry(1)] }));
    t.streams[0].push({ event: "log", entry: entry(2) });
    t.streams[0].push({ event: "log", entry: entry(2) });
    await settle();
    expect(t.dealer.view().state?.log.map((e) => e.id)).toEqual([1, 2]);
  });

  it("keeps an entry that came while the state was being read", async () => {
    const t = await started(dealerState({ log: [entry(1)] }));
    t.streams[0].push({ event: "changed" });
    await settle();
    t.streams[0].push({ event: "log", entry: entry(2) });
    t.streams[0].push({ event: "log", entry: entry(3) });
    await settle();
    t.reads[1].resolve(dealerState({ log: [entry(1), entry(2)] }));
    await settle();
    expect(t.dealer.view().state?.log.map((e) => e.id)).toEqual([1, 2, 3]);
  });

  it("starts over with a service that started over", async () => {
    const t = await started(dealerState({ log: [entry(1), entry(2)] }));
    await t.changed(dealerState({ log: [entry(1, { at: "2026-11-24T15:00:00Z" })] }));
    expect(t.dealer.view().state?.log).toHaveLength(1);
  });

  it("holds the latest 200", async () => {
    const t = await started(dealerState({ log: Array.from({ length: 200 }, (_, n) => entry(n)) }));
    t.streams[0].push({ event: "log", entry: entry(200) });
    await settle();
    const ids = t.dealer.view().state!.log.map((e) => e.id);
    expect(ids).toHaveLength(200);
    expect([ids[0], ids.at(-1)]).toEqual([1, 200]);
  });
});

describe("a change of the stock", () => {
  const shown = (d: Dealer) => d.view().state!.stock.spark.Göteborg;

  it("is shown at once, and stays", async () => {
    const t = await started();
    void t.dealer.changeStock("spark", "Göteborg", 1);
    void t.dealer.changeStock("spark", "Göteborg", 1);
    expect(shown(t.dealer)).toBe(4);
    t.writes[0].resolve();
    await settle();
    expect(shown(t.dealer)).toBe(4);
    t.writes[1].resolve();
    await settle();
    expect(shown(t.dealer)).toBe(4);
    expect(t.writes.map((w) => w.call)).toEqual(Array(2).fill(["changeStock", "spark", "Göteborg", 1]));
  });

  it("that fails puts the number back and says so", async () => {
    const t = await started();
    const done = t.dealer.changeStock("spark", "Göteborg", -1);
    expect(shown(t.dealer)).toBe(1);
    t.writes[0].reject(t.error(409, "There is none in stock in Göteborg."));
    await settle();
    expect(shown(t.dealer)).toBe(2);
    expect(t.dealer.view().notice).toBe("There is none in stock in Göteborg.");
    expect(await done).toBe(false);
    t.dealer.dismissNotice();
    expect(t.dealer.view().notice).toBeUndefined();
  });

  it("never goes below 0", async () => {
    const t = await started();
    void t.dealer.changeStock("spark", "Uppsala", -1);
    expect(t.dealer.view().state!.stock.spark.Uppsala).toBe(0);
    expect(t.writes).toEqual([]);
  });

  it("throws away a read that it overlaps, and reads after it", async () => {
    const t = await started();
    t.streams[0].push({ event: "changed" });
    await settle();
    void t.dealer.changeStock("spark", "Göteborg", 1);
    t.reads[1].resolve(dealerState());
    await settle();
    expect(shown(t.dealer)).toBe(3);
    expect(t.reads).toHaveLength(2);
    t.writes[0].resolve();
    await settle();
    expect(shown(t.dealer)).toBe(3);
    expect(t.reads).toHaveLength(3);
  });

  it("flashes when somebody else made it, and not for a click here", async () => {
    const t = await started();
    const flashes = () => t.dealer.view().flashes["spark Göteborg"] ?? 0;
    void t.dealer.changeStock("spark", "Göteborg", 1);
    t.writes[0].resolve();
    await settle();
    const now = dealerState();
    now.stock.spark.Göteborg = 3;
    t.reads.at(-1)!.resolve(now);
    await settle();
    expect(flashes()).toBe(0);
    const later = dealerState();
    later.stock.spark.Göteborg = 2;
    await t.changed(later);
    expect(flashes()).toBe(1);
  });
});

describe("an order", () => {
  it("is busy from the click until the state is read", async () => {
    const t = await started(dealerState({ orders: [order("VO-1001")] }));
    const done = t.dealer.deliver("VO-1001", "Malmö");
    void t.dealer.deliver("VO-1001", "Malmö");
    expect(t.dealer.view().busy.has("VO-1001")).toBe(true);
    expect(t.writes).toHaveLength(1);
    t.writes[0].resolve();
    await settle();
    expect(t.dealer.view().busy.has("VO-1001")).toBe(true);
    t.reads.at(-1)!.resolve(dealerState({ orders: [order("VO-1001", { status: "delivered", city: "Malmö" })] }));
    await settle();
    expect(await done).toBe(true);
    expect(t.dealer.view().busy.has("VO-1001")).toBe(false);
  });

  it("that fails is free again, with the reason", async () => {
    const t = await started(dealerState({ orders: [order("VO-1001")] }));
    const done = t.dealer.deliver("VO-1001", "Malmö");
    t.writes[0].reject(t.error(409, "This order is already delivered."));
    await settle();
    t.reads.at(-1)!.resolve(dealerState({ orders: [] }));
    await settle();
    expect(await done).toBe(false);
    expect(t.dealer.view().notice).toBe("This order is already delivered.");
    expect(t.dealer.view().busy.size).toBe(0);
  });

  it("change that is refused with a 403 closes the page", async () => {
    const t = await started(dealerState({ orders: [order("VO-1001")] }));
    void t.dealer.remove("VO-1001");
    t.writes[0].reject(t.error(403, "no"));
    await settle();
    expect(t.dealer.view().denied).toBe(true);
  });
});

describe("deliverOptions", () => {
  it("lists the cities with stock, most first", () => {
    expect(deliverOptions(dealerState().stock, "spark")).toEqual([
      { city: "Malmö", count: 5 },
      { city: "Stockholm", count: 4 },
      { city: "Göteborg", count: 2 },
    ]);
  });
  it("is empty at none, and for a model that is not there", () => {
    expect(deliverOptions(dealerState().stock, "fjord")).toEqual([]);
    expect(deliverOptions(dealerState().stock, "constructor")).toEqual([]);
  });
});
