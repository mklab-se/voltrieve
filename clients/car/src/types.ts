// Copied from clients/web; keep the two alike.
export type Backend = "loop" | "prompt" | "hosted";
export const BACKENDS: readonly Backend[] = ["loop", "prompt", "hosted"];

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

// From here on it is the car's own.

/** A tool this client runs, as it is sent to the API. */
export interface ClientTool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

/** What a tool gives back. The attachments go to the model with the output. */
export interface ToolOutput {
  output: string;
  attachments?: string[];
}

/** What the car knows and the driver does not have to say. */
export interface ClientContext {
  car?: { model?: string; trim?: string; drive?: string; model_year?: number; registration?: string; odometer_km?: number };
  /** ISO 8601 with an offset. */
  local_time?: string;
  speed_kmh?: number;
}
