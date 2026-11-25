/** The link to the API that brings commands to the car, for as long as remote control is on. */
import { SignInNeeded } from "./auth/types";
import type { CarState, Code, Command } from "./remote";
import { createSseParser } from "./sse";

/** The last three end it. "unavailable": the API does not take this car. "replaced": too many cars of the same
 * driver are linked. "sign_in": the driver has to sign in again. */
export type LinkStatus = "connecting" | "linked" | End;
type End = "unavailable" | "replaced" | "sign_in";

export interface RemoteLinkOptions {
  apiUrl: string;
  /** Without leaving the page. A SignInNeeded when that cannot be. */
  getToken: () => Promise<string>;
  state: () => CarState;
  /** Called when the API has agreed that the command is done now. */
  onCommand: (command: Command) => Promise<Code>;
  onStatus: (status: LinkStatus) => void;
  fetchImpl?: typeof fetch;
}

/** The API says something at least every 15 s. After three times that, nobody is there. */
export const SILENCE_MS = 45_000;
export const LOOK_MS = 500;
const FIRST_WAIT_MS = 1000;
const LONGEST_WAIT_MS = 30_000;
/** A message to the API that takes longer than this is given up. */
const POST_MS = 5000;
/** A command is done within this time from asking for it, or not at all: the API waits 2 s for the result. */
export const TAKE_MS = 1500;
/** A link that lasted this long was a good one, and the next waiting time is a short one again. */
const GOOD_MS = 10_000;

export const backoffMs = (failures: number) => Math.min(FIRST_WAIT_MS * 2 ** failures, LONGEST_WAIT_MS);

function parseCommand(data: string): Command | undefined {
  const { id, name, arguments: args, app } = JSON.parse(data) as Record<string, unknown>;
  if (typeof id !== "string" || typeof name !== "string" || typeof app !== "string") return undefined;
  if (typeof args !== "object" || args === null || Array.isArray(args)) return undefined;
  return { id, name, arguments: args as Record<string, unknown>, app };
}

export class RemoteLink {
  private readonly closing = new AbortController();
  private id?: string;
  /** What the API knows about the car. */
  private told?: CarState;
  private telling = false;
  /** How often telling has failed, and when to try again. */
  private untold = 0;
  private tellAt = 0;
  private token = "";

  constructor(private readonly options: RemoteLinkOptions) {}

  get closed(): boolean {
    return this.closing.signal.aborted;
  }

  async run(): Promise<void> {
    const looking = setInterval(() => void this.tell(), LOOK_MS);
    try {
      for (let failures = 0; !this.closed; ) {
        this.status("connecting");
        const start = Date.now();
        const end = await this.connect().catch((e: unknown) => (e instanceof SignInNeeded ? "sign_in" : "lost"));
        this.id = undefined;
        if (end !== "lost") {
          this.status(end);
          return;
        }
        failures = Date.now() - start >= GOOD_MS ? 0 : failures + 1;
        await this.wait(backoffMs(Math.max(failures - 1, 0)));
      }
    } finally {
      clearInterval(looking);
    }
  }

  close(): void {
    this.closing.abort();
  }

  private status(status: LinkStatus): void {
    if (!this.closed) this.options.onStatus(status);
  }

  private wait(ms: number): Promise<void> {
    if (this.closed) return Promise.resolve();
    return new Promise((resolve) => {
      const done = () => {
        clearTimeout(timer);
        this.closing.signal.removeEventListener("abort", done);
        resolve();
      };
      const timer = setTimeout(done, ms);
      this.closing.signal.addEventListener("abort", done);
    });
  }

  private post(path: string, headers: Record<string, string>, body: string | undefined, signal: AbortSignal): Promise<Response> {
    const { apiUrl, fetchImpl = fetch } = this.options;
    return fetchImpl(`${apiUrl.replace(/\/+$/, "")}/car/link${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, ...headers },
      body,
      signal,
    });
  }

  private say(path: string, body: unknown, ms = POST_MS): Promise<Response> {
    const signal = AbortSignal.any([this.closing.signal, AbortSignal.timeout(ms)]);
    return this.post(path, { "Content-Type": "application/json" }, JSON.stringify(body), signal);
  }

  /** One link, until it ends. "lost": try again, with a fresh token. */
  private async connect(): Promise<"lost" | End> {
    this.token = await this.options.getToken();
    if (this.closed) return "lost";
    const silent = new AbortController();
    const signal = AbortSignal.any([this.closing.signal, silent.signal]);
    let silence: ReturnType<typeof setTimeout> | undefined;
    const heard = () => {
      clearTimeout(silence);
      silence = setTimeout(() => silent.abort(), SILENCE_MS);
    };
    heard();
    try {
      const res = await this.post("", { Accept: "text/event-stream" }, undefined, signal);
      if (res.status === 403 || res.status === 404) return "unavailable";
      if (!res.ok || !res.body) return "lost";
      const parser = createSseParser();
      const decoder = new TextDecoder();
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return "lost";
        heard();
        for (const { event, data } of parser.feed(decoder.decode(value, { stream: true }))) {
          if (event === "link") this.linked((JSON.parse(data) as { id: string }).id);
          if (event === "command") void this.obey(data);
          if (event === "end" && (JSON.parse(data) as { reason: string }).reason === "replaced") return "replaced";
        }
      }
    } finally {
      clearTimeout(silence);
      silent.abort(); // whatever is left of the stream
    }
  }

  private linked(id: string): void {
    this.id = id;
    this.told = undefined;
    this.untold = this.tellAt = 0;
    this.status("linked");
    void this.tell();
  }

  private async obey(data: string): Promise<void> {
    const { id } = this;
    if (this.closed || !id) return;
    try {
      const command = parseCommand(data);
      if (!command) return;
      if (!(await this.take(id, command.id))) return;
      const code = await this.options.onCommand(command);
      const result = code === "done" ? { id: command.id, ok: true } : { id: command.id, ok: false, message: code };
      await this.say(`/${id}/results`, result);
    } catch {
      /* the API then says that the car did not answer */
    }
  }

  /** Whether someone still waits for the command. A late network must not honk the horn. */
  private async take(id: string, command: string): Promise<boolean> {
    const start = Date.now();
    const res = await this.say(`/${id}/take`, { id: command }, TAKE_MS);
    return res.ok && !this.closed && Date.now() - start < TAKE_MS; // a computer that slept was late too
  }

  /** The state, when it has changed. The kilometers go along with it, and never by themselves. */
  private async tell(): Promise<void> {
    const { id } = this;
    const state = this.options.state();
    if (!id || this.closed || this.telling || Date.now() < this.tellAt) return;
    if (this.told && this.told.parked === state.parked && this.told.wipers === state.wipers) return;
    this.telling = true; // one at a time, so that the last one sent is the last one to arrive
    try {
      const res = await this.say(`/${id}/state`, state);
      if (this.id !== id) return;
      if (!res.ok) throw new Error("not told");
      this.told = state;
      this.untold = 0;
    } catch {
      if (this.id !== id) return; // the new link is told at once
      this.tellAt = Date.now() + backoffMs(this.untold);
      this.untold += 1;
    } finally {
      this.telling = false;
    }
  }
}
