/** When the HUD is open. Pure: the time comes with each event. */

export const CLOSE_AFTER_MS = 12_000;

export interface HudState {
  open: boolean;
  /** The pointer or the keyboard focus is in it. */
  hold: boolean;
  /** Runa is working on an answer. */
  working: boolean;
  /** Since when nothing has happened. */
  quietSince?: number;
  /** The block that the answer is for, when the driver asked about a sign. */
  askedBlock?: number;
  /** The block of the last sign. */
  currentBlock?: number;
}

export type HudEvent = "send" | "delta" | "done" | "tick" | "escape" | "enter" | "leave";

export const initialHud: HudState = { open: false, hold: false, working: false };

export function hudReducer(state: HudState, event: HudEvent, nowMs: number): HudState {
  switch (event) {
    case "send":
      return { open: true, hold: false, working: true, currentBlock: state.currentBlock };
    case "delta":
      return { ...state, quietSince: nowMs };
    case "done":
      return { ...state, working: false, quietSince: nowMs };
    case "enter":
      return { ...state, hold: true };
    case "leave":
      return { ...state, hold: false, quietSince: nowMs };
    case "escape":
      return { ...state, open: false, hold: false };
    case "tick": {
      const quiet = state.quietSince !== undefined && nowMs - state.quietSince >= CLOSE_AFTER_MS;
      return state.open && quiet && !state.hold && !state.working ? { ...state, open: false } : state;
    }
  }
}

/** What the HUD says when the car has come to another sign since the question. */
export function hudNotice({ open, askedBlock, currentBlock }: HudState): string | undefined {
  if (!open || askedBlock === undefined || askedBlock === currentBlock) return undefined;
  return "This answer is for the previous block.";
}
