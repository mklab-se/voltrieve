// Copied from clients/web, with the texts of the car; keep the two alike.
const PHOTO_GONE = "The photo of the sign is no longer available. Ask again.";
const LOST = "The connection was lost before the answer was complete. Try again.";

const BY_CODE = new Map([
  ["auth", "You could not be signed in. Try again, or sign out and in again."],
  ["client", "Something went wrong in the car. Reload the page and try again."],
  ["http_401", "Your sign-in is no longer valid. Sign out and in again."],
  // A missing role, or a conversation that is unknown or too old.
  ["http_403", "Runa cannot open this chat. Choose New chat and ask again. If it keeps happening, your account may not have access."],
  ["http_409", "Runa is still answering your last question. Wait a moment and try again."],
  ["http_413", "Your message is too long. Make it shorter and try again."],
  ["http_503", "Voltrieve is not available right now. Try again in a moment."],
  ["network", "Could not reach Voltrieve. Try again."],
  ["stream", LOST],
  ["incomplete", LOST],
  ["unknown_attachment", PHOTO_GONE],
  ["turn_expired", "Runa waited too long for the car. Say it again."],
  ["conversation_recovery_failed", "This chat cannot go on. Choose New chat and ask again."],
  ["too_many_tool_calls", "Runa asked for too many things in a row. Try again."],
]);

/** What the driver reads about an error. What the API said, with its backends and roles, is for the timeline. */
export function customerText(code: string): string {
  return BY_CODE.get(code) ?? "Runa could not answer. Try again.";
}

class NotSignedIn extends Error {}

const textOf = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The token, or an error that says that it was the sign-in that failed. */
export function withSignIn(getToken: () => Promise<string>): () => Promise<string> {
  return () => getToken().catch((e: unknown) => Promise.reject(new NotSignedIn(textOf(e))));
}

/** What was thrown during a turn, as the data of an error event. */
export function errorOf(e: unknown): { code: string; message: string } {
  return { code: e instanceof NotSignedIn ? "auth" : "client", message: textOf(e) };
}
