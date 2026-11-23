const PHOTO_GONE = "The photo is no longer available. Try again, or remove it and ask without it.";
const LOST = "The connection was lost before the answer was complete. Try again.";

const BY_CODE = new Map([
  ["auth", "You could not be signed in. Try again, or sign out and in again."],
  ["http_401", "Your sign-in is no longer valid. Sign out and in again."],
  ["http_403", "Your account is not allowed to use this chat."],
  ["http_409", "Runa is still answering your last question. Wait a moment and try again."],
  ["http_413", "Your message is too long. Make it shorter and try again."],
  ["http_503", "Voltrieve is not available right now. Try again in a moment."],
  ["network", "Could not reach Voltrieve. Try again."],
  ["stream", LOST],
  ["incomplete", LOST],
  ["unknown_attachment", PHOTO_GONE],
  ["turn_expired", "Runa waited too long for your device. Send your question again."],
  ["conversation_recovery_failed", "This chat cannot go on. Choose New chat and ask again."],
  ["too_many_tool_calls", "Runa asked for too many things in a row. Try again."],
]);

/** What a customer reads about an error. What the API said, with its backends and roles, is for the timeline. */
export function customerText(code: string): string {
  return BY_CODE.get(code) ?? "Runa could not answer. Try again.";
}
