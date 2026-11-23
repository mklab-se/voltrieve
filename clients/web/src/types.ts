export type Backend = "loop" | "prompt" | "hosted";
export const BACKENDS: readonly Backend[] = ["loop", "prompt", "hosted"];

/** What the client knows, sent with a question. The API treats it as data and nothing else. */
export interface ClientContext {
  car: {
    model: string;
    trim: string;
    drive: string;
    model_year: number;
    registration: string;
    odometer_km: number;
  };
  local_time: string;
}

export interface TraceData {
  step: string;
  component: string;
  detail: string;
  elapsed_ms: number;
}

export interface ClientToolCall {
  call_id: string;
  name: string;
  arguments: unknown;
}

export interface DoneData {
  conversation_id: string;
  turn_id: string;
  backend: string;
  usage?: Record<string, unknown> | null;
}

/** A document an answer used ("source") or suggests ("read_more"). A link is an API path. */
export interface SourceItem {
  id: string;
  title: string;
  kind: "source" | "read_more";
  link?: string;
}

export type ServerEvent =
  | { type: "trace"; data: TraceData }
  | { type: "status"; data: { code: string; message: string } }
  | { type: "delta"; data: { text: string } }
  | { type: "client_tool_calls"; data: { turn_id?: string; conversation_id?: string; calls: ClientToolCall[] } }
  | { type: "sources"; data: { items: SourceItem[] } }
  | { type: "done"; data: DoneData }
  | { type: "error"; data: { code: string; message: string } };
