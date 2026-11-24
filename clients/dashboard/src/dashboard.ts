import { createSseParser } from "./sse";

/** One turn on the live feed, as GET /admin/live sends it. The prompt is null unless asked for. */
export interface FeedTurn {
  turn_id: string;
  at: number;
  user: string;
  backend: string;
  client?: string; // car, web, cli, other or test; absent on a turn from an older API
  prompt: string | null;
  kind?: "message" | "tool_results";
  outcome: "running" | "answered" | "curated" | "client_tool" | "error";
  error_code?: string | null;
  first_text_ms?: number | null;
  duration_ms?: number | null;
  tools?: string[];
  knowledge_bases?: string[];
  cost_sek?: number | null;
  model_calls?: number;
}

export interface FeedSummary {
  active_users: number;
  turns: number;
}

export interface FeedState {
  summary: FeedSummary;
  turns: FeedTurn[];
}

export type FeedMessage =
  | { event: "summary"; data: FeedSummary }
  | { event: "turn"; data: FeedTurn }
  | { event: "reset" }; // a new connection: the API sends the recent turns again

export const FEED_SIZE = 50;
export const DASHBOARD_ROLE = "Dashboard.View";
export const initialFeed: FeedState = { summary: { active_users: 0, turns: 0 }, turns: [] };

/** Whether the access token carries the dashboard role. The API checks it again. */
export function canViewDashboard(accessToken: string): boolean {
  try {
    const part = accessToken.split(".")[1];
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    const roles = (JSON.parse(json) as { roles?: unknown }).roles;
    return Array.isArray(roles) && roles.includes(DASHBOARD_ROLE);
  } catch {
    return false;
  }
}

/** Whether the signed-in user may open the feed. The API checks the role again on every request. */
export async function checkAccess(auth: { getToken(): Promise<string> }): Promise<boolean> {
  try {
    return canViewDashboard(await auth.getToken());
  } catch {
    return false;
  }
}

/** A turn is published when it starts and again when it ends: merge by id, newest first. */
export function feedReducer(state: FeedState, msg: FeedMessage): FeedState {
  if (msg.event === "reset") return { ...state, turns: [] };
  if (msg.event === "summary") return { ...state, summary: msg.data };
  const existing = state.turns.find((t) => t.turn_id === msg.data.turn_id);
  if (existing) {
    return { ...state, turns: state.turns.map((t) => (t === existing ? { ...t, ...msg.data } : t)) };
  }
  const turns = [msg.data, ...state.turns].sort((a, b) => b.at - a.at);
  return { ...state, turns: turns.slice(0, FEED_SIZE) };
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function counted(lists: (string[] | undefined)[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const list of lists) for (const name of list ?? []) counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

export interface FeedStats {
  medianFirstText: Record<string, number>;
  tools: [string, number][];
  knowledgeBases: [string, number][];
  costSek: number;
  running: number;
}

const BACKENDS = ["loop", "prompt", "hosted"];

function place(backend: string): number {
  const at = BACKENDS.indexOf(backend);
  return at < 0 ? BACKENDS.length : at;
}

/** What the tiles show, from the turns in the feed (not the whole day: see the workbook). */
export function stats(turns: FeedTurn[]): FeedStats {
  const byBackend = new Map<string, number[]>();
  for (const t of turns) {
    if (typeof t.first_text_ms !== "number") continue;
    byBackend.set(t.backend, [...(byBackend.get(t.backend) ?? []), t.first_text_ms]);
  }
  return {
    // In one order, so that the tiles stay where they are while the feed moves.
    medianFirstText: Object.fromEntries([...byBackend].sort(([a], [b]) => place(a) - place(b)).map(([b, v]) => [b, median(v)])),
    tools: counted(turns.map((t) => t.tools)),
    knowledgeBases: counted(turns.map((t) => t.knowledge_bases)),
    costSek: turns.reduce((sum, t) => sum + (t.cost_sek ?? 0), 0),
    running: turns.filter((t) => t.outcome === "running").length,
  };
}

export function buildLiveRequest(apiUrl: string, token: string, prompts: boolean): { url: string; init: RequestInit } {
  const base = `${apiUrl.replace(/\/+$/, "")}/admin/live`;
  return {
    url: prompts ? `${base}?prompts=true` : base,
    init: { headers: { Accept: "text/event-stream", Authorization: `Bearer ${token}` } },
  };
}

/** Stream the live feed until the signal aborts or the server ends it. */
export async function* streamLive(apiUrl: string, token: string, prompts: boolean, signal: AbortSignal): AsyncGenerator<FeedMessage> {
  const { url, init } = buildLiveRequest(apiUrl, token, prompts);
  const response = await fetch(url, { ...init, signal });
  if (!response.ok || !response.body) throw new Error(`The dashboard feed answered ${response.status}.`);
  const parser = createSseParser();
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  for (;;) {
    const { value, done } = await reader.read();
    const messages = done ? parser.flush() : parser.feed(value);
    for (const m of messages) {
      if (m.event === "summary" || m.event === "turn") yield { event: m.event, data: JSON.parse(m.data) } as FeedMessage;
    }
    if (done) return;
  }
}
