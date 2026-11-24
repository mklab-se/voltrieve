import type { Auth } from "./auth/types";
import { createSseParser } from "./sse";
import type { DealerState, LogEntry } from "./state";

export type DealerEvent = { event: "open" } | { event: "log"; entry: LogEntry } | { event: "changed" } | { event: "clear" };

export interface Api {
  state(signal: AbortSignal): Promise<DealerState>;
  /** Gives "open" when the stream is there, then what it sends, until it ends. */
  events(signal: AbortSignal): AsyncGenerator<DealerEvent>;
  changeStock(model: string, city: string, change: 1 | -1): Promise<void>;
  deliver(number: string, city: string): Promise<void>;
  remove(number: string): Promise<void>;
  reset(): Promise<void>;
}

/** An error whose message can be shown. The status is 0 when there was no answer. */
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const ANSWER_MS = 30_000;
/** The service sends a keepalive every 15 s. After this long without one, the connection is dead. */
const SILENCE_MS = 45_000;

const TEXTS: Record<number, string> = {
  401: "The sign-in has ended. Sign out, and sign in again.",
  403: "This account has no access.",
  404: "The order is no longer there.",
  503: "The dealer system is not available right now.",
};
const FAILED = "The dealer system could not do that.";

/** Only a 409 is worded by the service. */
async function refused(response: Response): Promise<ApiError> {
  let text = TEXTS[response.status] ?? FAILED;
  if (response.status === 409) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    text = typeof body?.detail === "string" && body.detail ? body.detail : "Somebody changed this first. Look again.";
  }
  return new ApiError(response.status, text);
}

function isState(body: unknown): body is DealerState {
  const s = body as Partial<DealerState> | null;
  return [s?.models, s?.cities, s?.orders, s?.log].every(Array.isArray) && typeof s?.stock === "object" && s.stock !== null;
}

export function createApi(auth: Pick<Auth, "getToken">): Api {
  /** One request with a fresh token. Only an answer that is ok comes back. */
  async function send(path: string, signal: AbortSignal, init: RequestInit = {}): Promise<Response> {
    let token: string;
    try {
      token = await auth.getToken();
    } catch {
      throw new ApiError(0, "The sign-in could not be renewed. Sign out, and sign in again.");
    }
    let response: Response;
    try {
      response = await fetch(path, { ...init, signal, headers: { ...init.headers, Authorization: `Bearer ${token}` } });
    } catch {
      throw new ApiError(0, "The dealer system did not answer.");
    }
    if (!response.ok) throw await refused(response);
    return response;
  }

  const soon = () => AbortSignal.timeout(ANSWER_MS);
  async function post(path: string, body?: object): Promise<void> {
    await send(path, soon(), { method: "POST", ...(body && { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }) });
  }
  const orderPath = (number: string) => `/staff/orders/${encodeURIComponent(number)}`;

  return {
    async state(signal) {
      const response = await send("/staff/state", AbortSignal.any([signal, soon()]), { cache: "no-store" });
      const body: unknown = await response.json().catch(() => null);
      if (!isState(body)) throw new ApiError(response.status, FAILED);
      return body;
    },

    async *events(signal) {
      const silent = new AbortController();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const heard = () => {
        clearTimeout(timer);
        timer = setTimeout(() => silent.abort(), SILENCE_MS);
      };
      try {
        heard();
        const response = await send("/staff/events", AbortSignal.any([signal, silent.signal]), { headers: { Accept: "text/event-stream" } });
        if (!response.body) throw new ApiError(response.status, FAILED);
        yield { event: "open" };
        const parser = createSseParser();
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
        for (;;) {
          const { value, done } = await reader.read();
          heard();
          for (const m of done ? parser.flush() : parser.feed(value)) {
            const known = read(m.event, m.data);
            if (known) yield known;
          }
          if (done) return;
        }
      } finally {
        clearTimeout(timer);
        silent.abort();
      }
    },

    changeStock: (model, city, change) => post("/staff/stock", { model, city, change }),
    deliver: (number, city) => post(`${orderPath(number)}/deliver`, { city }),
    remove: async (number) => void (await send(orderPath(number), soon(), { method: "DELETE" })),
    reset: () => post("/staff/reset"),
  };
}

function read(event: string, data: string): DealerEvent | undefined {
  if (event === "changed" || event === "clear") return { event };
  if (event !== "log") return undefined;
  try {
    return { event, entry: JSON.parse(data) as LogEntry };
  } catch {
    return undefined;
  }
}
