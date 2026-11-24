/** When the HUD is open. Pure: the time comes with each event. */

export const CLOSE_AFTER_MS = 12_000;

export interface HudState {
  open: boolean;
  /** The pointer is in it. */
  pointer: boolean;
  /** The keyboard focus is in it. */
  focus: boolean;
  /** Runa is working on an answer. */
  working: boolean;
  /** Since when nothing has happened. */
  quietSince?: number;
  /** The block that the answer is for, when the driver asked about a sign. */
  askedBlock?: number;
  /** The block of the last sign. */
  currentBlock?: number;
}

export type HudEvent = "send" | "delta" | "done" | "tick" | "escape" | "pointerenter" | "pointerleave" | "focus" | "blur";

export const initialHud: HudState = { open: false, pointer: false, focus: false, working: false };

export function hudReducer(state: HudState, event: HudEvent, nowMs: number): HudState {
  switch (event) {
    case "send":
      // What is in it stays in it: nothing enters again when the HUD was open already.
      return { ...state, open: true, working: true, quietSince: undefined, askedBlock: undefined };
    case "delta":
      return { ...state, quietSince: nowMs };
    case "done":
      return { ...state, working: false, quietSince: nowMs };
    case "pointerenter":
      return { ...state, pointer: true };
    case "pointerleave":
      return { ...state, pointer: false, quietSince: nowMs };
    case "focus":
      return { ...state, focus: true };
    case "blur":
      return { ...state, focus: false, quietSince: nowMs };
    case "escape":
      return { ...state, open: false, pointer: false, focus: false };
    case "tick": {
      const quiet = state.quietSince !== undefined && nowMs - state.quietSince >= CLOSE_AFTER_MS;
      const held = state.pointer || state.focus || state.working;
      return state.open && quiet && !held ? { ...state, open: false } : state;
    }
  }
}

/** What the HUD says when the car has come to another sign since the question. */
export function hudNotice({ open, askedBlock, currentBlock }: HudState): string | undefined {
  if (!open || askedBlock === undefined || askedBlock === currentBlock) return undefined;
  return "This answer is for the previous block.";
}
