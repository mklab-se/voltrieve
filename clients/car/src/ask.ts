/** Small decisions of the cockpit that are easier to test on their own. */
import type { Pedal } from "./pedals";
import type { ClientToolCall, ToolOutput } from "./types";

export const PARK_QUESTION = "Can I park here?";

/**
 * The question about the last sign, with its photo. When the photo cannot be used, the question goes
 * alone: Runa then asks the car for the photo, gets to know why, and tells the driver.
 */
export async function askToPark(
  signPhoto: () => Promise<{ id?: string }>,
  send: (text: string, attachments?: string[]) => void,
  /** False when a new chat has begun meanwhile. */
  current: () => boolean = () => true,
): Promise<void> {
  const id = await signPhoto().then((photo) => photo.id, () => undefined);
  if (!current()) return;
  send(PARK_QUESTION, id === undefined ? undefined : [id]);
}

/** What the HUD says while the car does something that takes time. */
const DOING: Record<string, string | undefined> = { park: "Parking the car", get_sign_photo: "Looking at the sign" };

/** Run a tool, and show that it runs, unless a new chat begins meanwhile. */
export async function runShown(
  call: ClientToolCall,
  run: (call: ClientToolCall) => Promise<ToolOutput>,
  on: { current: () => boolean; doing: (what: string | undefined) => void; asked: () => void },
): Promise<ToolOutput> {
  on.doing(DOING[call.name]);
  try {
    const result = await run(call);
    if (result.attachments && on.current()) on.asked();
    return result;
  } finally {
    if (on.current()) on.doing(undefined);
  }
}

interface Key {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  isComposing: boolean;
}

/** Fields get their own keys. */
export const isField = (tagName?: string) => tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT";

/** True when the key is the start of something to say to Runa. */
export function typesInto(e: Key, focused?: string): boolean {
  if (isField(focused) || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return false;
  return [...e.key].length === 1 && e.key !== " ";
}

interface Focused {
  tagName: string;
  closest(selector: string): { scrollHeight: number; clientHeight: number } | null;
}

/** The pedal of an up or down key. None in a field, and none where the keys scroll. */
export function drivesWith(
  e: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean },
  focused: Focused | null,
): Pedal | undefined {
  const pedal = e.key === "ArrowUp" ? "accelerate" : e.key === "ArrowDown" ? "brake" : undefined;
  if (!pedal || e.ctrlKey || e.metaKey || e.altKey || isField(focused?.tagName)) return undefined;
  const region = focused?.closest("[data-scroll]");
  return region && region.scrollHeight > region.clientHeight ? undefined : pedal;
}
