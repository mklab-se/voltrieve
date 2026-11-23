import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Renewed } from "./attachments";
import type { Auth } from "./auth";
import { streamChat, streamToolResults } from "./chat";
import type { AppConfig } from "./config";
import { chatReducer, initialChatState, type ChatState } from "./turns";
import type { Backend, ClientContext, ClientTool, ClientToolCall, ServerEvent, ToolOutput } from "./types";

/** A tool result can lead to another tool call, but not forever. */
export const MAX_TOOL_ROUNDS = 5;
/** The API takes this many attachments in one request. */
const MAX_ATTACHMENTS = 3;

export interface ChatOptions {
  auth: Pick<Auth, "getToken">;
  config: Pick<AppConfig, "apiUrl">;
  backend: Backend;
  tools: ClientTool[];
  runTool: (call: ClientToolCall) => Promise<ToolOutput>;
  context: () => ClientContext;
  /** New ids for attachments that the API has lost. */
  renew?: (ids: string[]) => Promise<Renewed>;
}

export interface TurnInput extends ChatOptions {
  message: string;
  attachments?: string[];
  conversationId?: string;
  correlationId: string;
  signal?: AbortSignal;
}

interface Pause {
  turnId: string;
  conversationId: string;
  calls: ClientToolCall[];
}

interface Answer {
  pause?: Pause;
  /** The API has lost an attachment. */
  lost?: ServerEvent;
}

type Result = { call_id: string } & ToolOutput;
/** Send the request of a round, with these attachments. */
type Request = (attachments: string[]) => Promise<AsyncIterable<ServerEvent>>;

const UNKNOWN_ATTACHMENT = "unknown_attachment";

const failure = (code: string, message: string): ServerEvent => ({ type: "error", data: { code, message } });

const textOf = (e: unknown, otherwise: string) => (e instanceof Error && e.message ? e.message : otherwise);

/** One message from start to end: send it, run the tools the model asks for, and go on with the results. */
export async function runTurn(
  input: TurnInput,
  emit: (event: ServerEvent) => void,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const { auth, signal } = input;
  const base = { apiUrl: input.config.apiUrl, backend: input.backend, correlationId: input.correlationId };
  const show = (event: ServerEvent) => {
    if (!signal?.aborted) emit(event);
  };
  try {
    const context = input.context(); // once, so that the whole turn is about one moment
    let attachments = input.attachments ?? [];
    let request: Request = async (ids) =>
      streamChat(
        {
          ...base,
          token: await auth.getToken(),
          conversationId: input.conversationId,
          message: input.message,
          clientTools: input.tools,
          context,
          attachments: ids,
        },
        signal,
        fetchImpl,
      );
    let renewed = false;
    for (let round = 0; ; round += 1) {
      let answer = await relay(await request(attachments), show);
      if (answer.lost && input.renew && !renewed && !signal?.aborted) {
        renewed = true; // once in a turn
        const fresh = await input.renew(attachments);
        if (signal?.aborted) return;
        if ("message" in fresh) return show(failure(UNKNOWN_ATTACHMENT, fresh.message));
        answer = await relay(await request(fresh.ids), show);
      }
      if (answer.lost) return show(answer.lost);
      const { pause } = answer;
      if (!pause || signal?.aborted) return;
      if (round === MAX_TOOL_ROUNDS) {
        return show(failure("too_many_tool_calls", "Runa asked for too many things in a row. Try again."));
      }
      const results = await runTools(pause.calls, input.runTool, signal);
      if (signal?.aborted) return;
      attachments = results.flatMap((r) => r.attachments ?? []);
      request = async (ids) => {
        const { turnId, conversationId } = pause;
        const named = replaceIds(results, ids);
        return streamToolResults({ ...base, token: await auth.getToken(), conversationId, turnId, results: named }, signal, fetchImpl);
      };
    }
  } catch (e) {
    show(failure("auth", textOf(e, "Could not get a token.")));
  }
}

/** The results with other ids in place of theirs, in the same order. */
function replaceIds(results: Result[], ids: string[]): Result[] {
  let next = 0;
  return results.map((r) => (r.attachments ? { ...r, attachments: r.attachments.map(() => ids[next++]!) } : r));
}

/** Show the events of one answer. The result says what to do when the turn waits for this client. */
async function relay(events: AsyncIterable<ServerEvent>, show: (event: ServerEvent) => void): Promise<Answer> {
  const answer: Answer = {};
  for await (const event of events) {
    if (event.type === "error" && event.data.code === UNKNOWN_ATTACHMENT) {
      answer.lost = event; // not shown yet, it may be put right
      continue;
    }
    show(event);
    if (event.type !== "client_tool_calls") continue;
    const { turn_id: turnId, conversation_id: conversationId, calls } = event.data;
    if (turnId && conversationId) answer.pause = { turnId, conversationId, calls };
    else show(failure("malformed_event", "The API asked for a tool without saying for which turn."));
  }
  return answer;
}

/** One at a time and in order: to stop and then to park is not the same as to park and then to stop. */
async function runTools(calls: ClientToolCall[], runTool: ChatOptions["runTool"], signal?: AbortSignal) {
  const results: Result[] = [];
  const named = new Set<string>(); // the API takes an id once in a request
  for (const call of calls) {
    if (signal?.aborted) break;
    let result: ToolOutput;
    try {
      result = await runTool(call);
    } catch (e) {
      result = { output: `Error: ${textOf(e, "the tool failed.")}` };
    }
    const attachments: string[] = [];
    for (const id of result.attachments ?? []) {
      if (named.has(id) || named.size === MAX_ATTACHMENTS) continue;
      named.add(id);
      attachments.push(id);
    }
    results.push({ call_id: call.call_id, output: result.output, ...(attachments.length > 0 && { attachments }) });
  }
  return results;
}

export interface UseChat {
  state: ChatState;
  busy: boolean;
  send(text: string, attachments?: string[]): void;
  reset(): void;
}

export function useChat(options: ChatOptions): UseChat {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [busy, setBusy] = useState(false);
  const running = useRef<AbortController | null>(null);
  const latest = useRef({ options, conversationId: state.conversationId });
  latest.current = { options, conversationId: state.conversationId };

  const stop = useCallback(() => {
    running.current?.abort();
    running.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  const send = useCallback((message: string, attachments?: string[]) => {
    if (running.current) return; // one turn at a time
    const ctrl = new AbortController();
    running.current = ctrl;
    setBusy(true);
    const correlationId = crypto.randomUUID();
    const { options, conversationId } = latest.current;
    dispatch({ type: "start", message, backend: options.backend, correlationId });
    const turn = { ...options, message, attachments, conversationId, correlationId, signal: ctrl.signal };
    void runTurn(turn, (event) => dispatch({ type: "event", event })).finally(() => {
      if (running.current !== ctrl) return;
      running.current = null;
      setBusy(false);
    });
  }, []);

  const reset = useCallback(() => {
    stop();
    setBusy(false);
    dispatch({ type: "reset" });
  }, [stop]);

  return { state, busy, send, reset };
}
