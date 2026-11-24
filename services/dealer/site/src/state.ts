import { useEffect, useMemo, useSyncExternalStore } from "react";
import { ApiError, type Api, type DealerEvent } from "./api";

export interface Model {
  slug: string;
  name: string;
  price: number;
}

export interface Order {
  number: string;
  model: string;
  price_sek: number;
  name: string;
  address: string;
  client: string;
  status: "open" | "delivered";
  created: string;
  city: string | null;
  delivered: string | null;
  delivered_by: string | null;
}

export interface LogEntry {
  id: string | number;
  at: string;
  kind: string;
  who: string | null;
  client: string | null;
  model: string | null;
  city: string | null;
  number: string | null;
  result: string | null;
  correlation_id: string | null;
}

export type Stock = Record<string, Record<string, number>>;

/** GET /staff/state. Orders are newest first, the log has the latest last. */
export interface DealerState {
  models: Model[];
  cities: string[];
  stock: Stock;
  orders: Order[];
  log: LogEntry[];
}

export interface DealerView {
  /** With this page's own changes in it, also the ones on their way. */
  state?: DealerState;
  /** Orders that came while the page was open and are not dismissed, the oldest first. */
  arrived: Order[];
  live: boolean;
  /** Not live, but it has been. */
  lost: boolean;
  /** The service answered 403. Nothing is shown then. */
  denied: boolean;
  /** Why the page shows old data. Goes away by itself. */
  problem?: string;
  /** Why a change was not made. Stays until the next change, or until it is closed. */
  notice?: string;
  /** What is being changed: order numbers, and RESET. */
  busy: ReadonlySet<string>;
  /** How often a count was changed from elsewhere, by `stockKey`. */
  flashes: Readonly<Record<string, number>>;
}

export interface Dealer {
  view(): DealerView;
  subscribe(listener: () => void): () => void;
  /** Reads and listens until the function it gives is called. */
  start(): () => void;
  dismiss(number: string): void;
  dismissNotice(): void;
  changeStock(model: string, city: string, change: 1 | -1): Promise<boolean>;
  deliver(number: string, city: string): Promise<boolean>;
  remove(number: string): Promise<boolean>;
  reset(): Promise<boolean>;
}

export const RESET = "reset";
const READ_EVERY_MS = 60_000;
const READ_AGAIN_MS = 10_000;
const RECONNECT_MS = [1_000, 2_000, 5_000, 10_000];
const LOG_SIZE = 200;

export const stockKey = (model: string, city: string) => `${model} ${city}`;

export function countOf(stock: Stock, model: string, city: string): number {
  return (Object.hasOwn(stock, model) && stock[model][city]) || 0;
}

/** The cities that have the model, the one with the most first. */
export function deliverOptions(stock: Stock, model: string): { city: string; count: number }[] {
  if (!Object.hasOwn(stock, model)) return [];
  return Object.entries(stock[model])
    .map(([city, count]) => ({ city, count }))
    .filter((o) => o.count > 0)
    .sort((a, b) => b.count - a.count);
}

/** An id alone is not enough: a service that started over counts from the start again. */
export const entryKey = (e: LogEntry) => `${e.id} ${e.at}`;

function withEntry(log: LogEntry[], entry: LogEntry): LogEntry[] {
  return log.some((e) => entryKey(e) === entryKey(entry)) ? log : [...log, entry].slice(-LOG_SIZE);
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => (clearTimeout(timer), resolve()), { once: true });
  });
}

const textOf = (e: unknown) => (e instanceof ApiError ? e.message : "The dealer system could not do that.");

/** What the page knows, and what it can do. It has no React in it. */
export function createDealer(api: Api): Dealer {
  const listeners = new Set<() => void>();
  let base: DealerState | undefined; // as the service last told it
  let known: Set<string> | undefined; // order numbers that never arrive: the first ones, and the dismissed
  const pending = new Map<string, number>(); // own stock changes that are on their way
  let live = false;
  let lost = false;
  let denied = false;
  let problem: string | undefined;
  let notice: string | undefined;
  const busy = new Set<string>();
  let flashes: Record<string, number> = {};

  let run: AbortController | undefined; // from start to stop
  let wanted = false; // a read is needed
  let reading: { stale: boolean; entries: LogEntry[] } | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined; // after a read that failed
  let writes = 0;
  let readers: (() => void)[] = []; // who waits for the next read to end

  let view: DealerView;
  function emit() {
    const orders = base?.orders ?? [];
    view = {
      state: base && { ...base, stock: shownStock(base.stock) },
      arrived: known ? orders.filter((o) => !known!.has(o.number)).reverse() : [],
      live, lost, denied, problem, notice, busy: new Set(busy), flashes,
    };
    listeners.forEach((l) => l());
  }

  function shownStock(stock: Stock): Stock {
    if (pending.size === 0) return stock;
    const shown: Stock = {};
    for (const [model, cities] of Object.entries(stock)) {
      shown[model] = { ...cities };
      for (const city of Object.keys(cities)) shown[model][city] = Math.max(0, cities[city] + (pending.get(stockKey(model, city)) ?? 0));
    }
    return shown;
  }

  function deny() {
    denied = true;
    base = undefined;
    notice = problem = undefined;
    run?.abort();
  }

  // Reading: one at a time, and not while a change of our own is on its way, since nobody could tell
  // whether the answer holds it.
  async function read() {
    const mine = run;
    if (!mine || mine.signal.aborted || reading || writes > 0 || !wanted) return;
    wanted = false;
    const now = (reading = { stale: false, entries: [] as LogEntry[] });
    try {
      const state = await api.state(mine.signal);
      if (mine !== run || mine.signal.aborted) return;
      if (now.stale) wanted = true;
      else apply(state, now.entries);
    } catch (e) {
      if (mine !== run || mine.signal.aborted) return;
      if (e instanceof ApiError && e.status === 403) deny();
      else {
        problem = textOf(e);
        clearTimeout(retry);
        retry = setTimeout(readSoon, READ_AGAIN_MS);
      }
    } finally {
      if (reading === now) reading = undefined;
      if (mine === run) {
        if (!wanted || mine.signal.aborted) readers.splice(0).forEach((done) => done());
        emit();
        void read();
      }
    }
  }

  function readSoon() {
    wanted = true;
    void read();
  }

  function apply(state: DealerState, meanwhile: LogEntry[]) {
    if (base) {
      for (const [model, cities] of Object.entries(state.stock)) {
        for (const [city, count] of Object.entries(cities)) {
          if (count === countOf(base.stock, model, city)) continue;
          const key = stockKey(model, city);
          flashes = { ...flashes, [key]: (flashes[key] ?? 0) + 1 };
        }
      }
    }
    clearTimeout(retry);
    known ??= new Set(state.orders.map((o) => o.number));
    base = { ...state, log: meanwhile.reduce(withEntry, state.log) };
    if (notice === problem) notice = undefined;
    problem = undefined;
  }

  function heard(m: DealerEvent) {
    if (m.event === "open") {
      live = true;
      lost = false;
      wanted = true;
    } else if (m.event === "log") {
      if (base) base = { ...base, log: withEntry(base.log, m.entry) };
      reading?.entries.push(m.entry);
    } else {
      if (m.event === "clear") {
        if (base) base = { ...base, log: [] };
        if (reading) reading.stale = true;
      }
      wanted = true;
    }
    emit();
    void read();
  }

  async function listen(mine: AbortController) {
    for (let attempt = 0; !mine.signal.aborted; attempt++) {
      try {
        for await (const m of api.events(mine.signal)) {
          if (mine.signal.aborted) break;
          if (m.event === "open") attempt = 0;
          heard(m);
        }
      } catch (e) {
        if (mine === run && !mine.signal.aborted && e instanceof ApiError && e.status === 403) deny();
      }
      if (mine === run) {
        lost ||= live;
        live = false;
        emit();
      }
      await wait(RECONNECT_MS[Math.min(attempt, RECONNECT_MS.length - 1)], mine.signal);
    }
  }

  /** One change of our own. A read that it overlaps is thrown away, and one comes after it. */
  async function write(call: () => Promise<void>, after: (ok: boolean) => void = () => {}): Promise<boolean> {
    notice = undefined;
    writes++;
    if (reading) reading.stale = true;
    emit();
    let ok = false;
    try {
      await call();
      ok = true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) deny();
      else notice = textOf(e);
    }
    writes--;
    after(ok);
    emit();
    return ok;
  }

  async function writeBusy(key: string, call: () => Promise<void>): Promise<boolean> {
    if (busy.has(key)) return false;
    busy.add(key);
    const ok = await write(call);
    const done = new Promise<void>((resolve) => readers.push(resolve));
    readSoon();
    if (run && !run.signal.aborted) await done;
    busy.delete(key);
    emit();
    return ok;
  }

  emit();
  return {
    view: () => view,
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    start() {
      const mine = (run = new AbortController());
      denied = false;
      const timer = setInterval(readSoon, READ_EVERY_MS);
      mine.signal.addEventListener("abort", () => (clearInterval(timer), clearTimeout(retry)));
      readSoon();
      void listen(mine);
      return () => {
        mine.abort();
        if (mine !== run) return;
        run = undefined;
        reading = undefined;
        live = lost = false;
        readers.splice(0).forEach((done) => done());
        emit();
      };
    },
    dismiss(number) {
      known?.add(number);
      emit();
    },
    dismissNotice() {
      notice = undefined;
      emit();
    },
    async changeStock(model, city, change) {
      const key = stockKey(model, city);
      if (!base || countOf(base.stock, model, city) + (pending.get(key) ?? 0) + change < 0) return false;
      const add = (n: number) => {
        const sum = (pending.get(key) ?? 0) + n;
        if (sum) pending.set(key, sum);
        else pending.delete(key);
      };
      add(change);
      const ok = await write(
        () => api.changeStock(model, city, change),
        (made) => {
          add(-change);
          if (made && base) base = { ...base, stock: { ...base.stock, [model]: { ...base.stock[model], [city]: countOf(base.stock, model, city) + change } } };
        },
      );
      readSoon();
      return ok;
    },
    deliver: (number, city) => writeBusy(number, () => api.deliver(number, city)),
    remove: (number) => writeBusy(number, () => api.remove(number)),
    reset: () => writeBusy(RESET, () => api.reset()),
  };
}

export function useDealer(api: Api) {
  const dealer = useMemo(() => createDealer(api), [api]);
  useEffect(() => dealer.start(), [dealer]);
  const view = useSyncExternalStore(dealer.subscribe, dealer.view);
  return { ...view, dismiss: dealer.dismiss, dismissNotice: dealer.dismissNotice, changeStock: dealer.changeStock, deliver: dealer.deliver, remove: dealer.remove, reset: dealer.reset };
}
