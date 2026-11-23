import type { Backend, ServerEvent, SourceItem } from "./types";

export type TimelineEntry =
  | { kind: "trace"; step: string; component: string; detail: string; elapsedMs: number }
  | { kind: "done"; backend: string; turnId: string; usage: Record<string, unknown> | null }
  | { kind: "error"; code: string; message: string };

export interface Turn {
  id: number;
  userText: string;
  /** The photo the question is about. */
  photo?: Blob;
  /** Sent as X-Correlation-Id: the id to search for in logs and traces. */
  correlationId: string;
  requestedBackend: Backend;
  answer: string;
  /** What the agent says it is doing, until the answer starts. */
  activity?: string;
  entries: TimelineEntry[];
  status: "streaming" | "done" | "error";
  error?: string;
  /** Documents the answer used, and documents to read more. */
  sources?: SourceItem[];
  /** Backend that the API reports in its done event. */
  backend?: string;
}

export interface ChatState {
  conversationId?: string;
  turns: Turn[];
  /** The text in the field. */
  draft: string;
}

export type ChatAction =
  | { type: "start"; message: string; backend: Backend; correlationId: string; photo?: Blob }
  /** Take back the last turn, which was not sent, with its text in the field again. */
  | { type: "withdraw" }
  | { type: "draft"; text: string }
  | { type: "event"; event: ServerEvent }
  | { type: "reset" };

export const initialChatState: ChatState = { turns: [], draft: "" };

function updateLast(state: ChatState, fn: (t: Turn) => Turn): ChatState {
  if (state.turns.length === 0) return state;
  const turns = state.turns.slice();
  turns[turns.length - 1] = fn(turns[turns.length - 1]!);
  return { ...state, turns };
}

function fail(t: Turn, code: string, message: string): Turn {
  return { ...t, status: "error", error: message, entries: [...t.entries, { kind: "error", code, message }] };
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset":
      return { ...initialChatState, draft: state.draft };
    case "draft":
      return { ...state, draft: action.text };
    case "withdraw": {
      const text = state.turns[state.turns.length - 1]?.userText;
      const draft = [text, state.draft].filter(Boolean).join("\n");
      return { ...state, turns: state.turns.slice(0, -1), draft };
    }
    case "start": {
      const turn: Turn = {
        id: state.turns.length + 1,
        userText: action.message,
        photo: action.photo,
        correlationId: action.correlationId,
        requestedBackend: action.backend,
        answer: "",
        entries: [],
        status: "streaming",
      };
      // A suggested question does not come from the field.
      const draft = state.draft.trim() === action.message ? "" : state.draft;
      return { ...state, turns: [...state.turns, turn], draft };
    }
    case "event": {
      const ev = action.event;
      switch (ev.type) {
        case "status":
          return updateLast(state, (t) => ({ ...t, activity: ev.data.message }));
        case "trace":
          return updateLast(state, (t) => ({
            ...t,
            entries: [
              ...t.entries,
              {
                kind: "trace",
                step: ev.data.step,
                component: ev.data.component,
                detail: ev.data.detail,
                elapsedMs: ev.data.elapsed_ms,
              },
            ],
          }));
        case "delta":
          return updateLast(state, (t) => ({ ...t, answer: t.answer + ev.data.text }));
        case "client_tool_calls":
          // The app runs the tools and continues the turn. Later requests need this id.
          return {
            ...updateLast(state, (t) => ({
              ...t,
              entries: [
                ...t.entries,
                ...ev.data.calls.map((c): TimelineEntry => ({
                  kind: "trace",
                  step: "client_tool_ran",
                  component: "client",
                  detail: c.name,
                  elapsedMs: 0,
                })),
              ],
            })),
            conversationId: ev.data.conversation_id ?? state.conversationId,
          };
        case "sources":
          return updateLast(state, (t) => ({ ...t, sources: ev.data.items }));
        case "done":
          return {
            ...updateLast(state, (t) => ({
              ...t,
              status: t.status === "error" ? "error" : "done",
              backend: ev.data.backend,
              entries: [
                ...t.entries,
                { kind: "done", backend: ev.data.backend, turnId: ev.data.turn_id, usage: ev.data.usage ?? null },
              ],
            })),
            conversationId: ev.data.conversation_id || state.conversationId,
          };
        case "error":
          return updateLast(state, (t) => fail(t, ev.data.code, ev.data.message));
      }
    }
  }
}
