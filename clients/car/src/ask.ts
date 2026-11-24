/** Small decisions of the cockpit that are easier to test on their own. */

export const PARK_QUESTION = "Can I park here?";

/**
 * The question about the last sign, with its photo. When the photo cannot be used, the question goes
 * alone: Runa then asks the car for the photo, gets to know why, and tells the driver.
 */
export async function askToPark(
  signPhoto: () => Promise<{ id?: string }>,
  send: (text: string, attachments?: string[]) => void,
): Promise<void> {
  const id = await signPhoto().then((photo) => photo.id, () => undefined);
  send(PARK_QUESTION, id === undefined ? undefined : [id]);
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
