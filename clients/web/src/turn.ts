import type { ClientToolCall, ServerEvent } from "./types";

/** A client tool result can lead to another tool call, but not forever. */
export const MAX_TOOL_ROUNDS = 5;

/** A turn that waits for what this client's tools say. */
export interface Pause {
  turnId: string;
  conversationId: string;
  calls: ClientToolCall[];
}

export interface TurnIo {
  emit: (event: ServerEvent) => void;
  runTool: (call: ClientToolCall) => string;
  /** The events that follow the results. */
  resume: (pause: Pause, results: { call_id: string; output: string }[]) => Promise<AsyncIterable<ServerEvent>>;
}

const failure = (code: string, message: string): ServerEvent => ({ type: "error", data: { code, message } });

/** Show the events of a turn, and run the tools it asks for until it is done. It never ends without an end. */
export async function runTurn(events: AsyncIterable<ServerEvent>, { emit, runTool, resume }: TurnIo): Promise<void> {
  for (let round = 0; ; round += 1) {
    let pause: Pause | undefined;
    let asked: ServerEvent | undefined;
    let failed = false;
    for await (const event of events) {
      if (event.type === "client_tool_calls") {
        const { turn_id: turnId, conversation_id: conversationId, calls } = event.data;
        if (round === MAX_TOOL_ROUNDS) {
          return emit(failure("too_many_tool_calls", `The turn asked for client tools after ${MAX_TOOL_ROUNDS} rounds of them.`));
        }
        if (!turnId || !conversationId) {
          return emit(failure("malformed_event", "The API asked for a tool without saying for which turn."));
        }
        pause = { turnId, conversationId, calls };
        asked = event; // not shown until it is sure that the tools will run
        continue;
      }
      failed ||= event.type === "error";
      emit(event);
    }
    if (!pause || !asked || failed) return;
    emit(asked);
    const results = pause.calls.map((call) => ({ call_id: call.call_id, output: runTool(call) }));
    events = await resume(pause, results);
  }
}
