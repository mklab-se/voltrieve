import { ApiError, type Api, type DealerEvent } from "../api";
import type { DealerState, LogEntry, Order } from "../state";

export function order(number: string, over: Partial<Order> = {}): Order {
  return {
    number, model: "fjord", price_sek: 529000, name: "Kim Berg", address: "Storgatan 1, Uppsala", client: "web",
    status: "open", created: "2026-11-24T13:02:11Z", city: null, delivered: null, delivered_by: null, ...over,
  };
}

export function entry(id: number, over: Partial<LogEntry> = {}): LogEntry {
  return {
    id, at: "2026-11-24T13:02:11Z", kind: "stock", who: "Runa", client: null, model: null, city: null, number: null,
    result: null, correlation_id: null, ...over,
  };
}

export function dealerState(over: Partial<DealerState> = {}): DealerState {
  return {
    models: [{ slug: "spark", name: "Spark", price: 289000 }, { slug: "fjord", name: "Fjord", price: 529000 }],
    cities: ["Stockholm", "Göteborg", "Malmö", "Uppsala"],
    stock: {
      spark: { Stockholm: 4, Göteborg: 2, Malmö: 5, Uppsala: 0 },
      fjord: { Stockholm: 0, Göteborg: 0, Malmö: 0, Uppsala: 0 },
    },
    orders: [],
    log: [],
    ...over,
  };
}

type Answer<T> = { resolve: (value: T) => void; reject: (e: unknown) => void };

/** An API whose answers the test gives, one by one. */
export function fakeApi() {
  const reads: Answer<DealerState>[] = [];
  const writes: (Answer<void> & { call: unknown[] })[] = [];
  const streams: { push: (m: DealerEvent) => void; end: (e?: unknown) => void; closed: boolean }[] = [];
  const write = (...call: unknown[]) => new Promise<void>((resolve, reject) => writes.push({ call, resolve, reject }));
  const api: Api = {
    state: (signal) =>
      new Promise((resolve, reject) => {
        reads.push({ resolve, reject });
        signal.addEventListener("abort", () => reject(signal.reason));
      }),
    async *events(signal) {
      const queue: DealerEvent[] = [];
      let ended = false;
      let failure: unknown;
      let wake = () => {};
      const stream = {
        closed: false,
        push: (m: DealerEvent) => void (queue.push(m), wake()),
        end: (e?: unknown) => void ((ended = true), (failure = e), wake()),
      };
      streams.push(stream);
      signal.addEventListener("abort", () => stream.end(signal.reason));
      try {
        for (;;) {
          while (queue.length) yield queue.shift()!;
          if (failure) throw failure;
          if (ended) return;
          await new Promise<void>((resolve) => (wake = resolve));
        }
      } finally {
        stream.closed = true;
      }
    },
    changeStock: (model, city, change) => write("changeStock", model, city, change),
    deliver: (number, city) => write("deliver", number, city),
    remove: (number) => write("remove", number),
    reset: () => write("reset"),
  };
  return { api, reads, writes, streams, error: (status: number, text: string) => new ApiError(status, text) };
}

/** Lets everything that waits for a promise go on. */
export async function settle(): Promise<void> {
  for (let n = 0; n < 30; n++) await Promise.resolve();
}
