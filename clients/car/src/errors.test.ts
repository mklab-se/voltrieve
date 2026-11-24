// Copied from clients/web; keep the two alike.
import { describe, expect, it } from "vitest";
import { customerText, errorOf, withSignIn } from "./errors";
import { chatReducer, initialChatState } from "./turns";

/** Every code the API and this client give an error, with the text the API sends. */
const RAW: [string, string][] = [
  ["agent_unavailable", "The hosted agent could not complete the answer."],
  ["unsupported_client_tool", "The prompt agent only supports the client tools in its definition (set_theme). Not supported: x."],
  ["too_many_iterations", "The agent did not finish within the allowed number of model calls."],
  ["model_failed", "The model could not complete the answer."],
  ["model_incomplete", "The model stopped before finishing the answer."],
  ["model_error", "The model call failed."],
  ["internal_error", "The request could not be completed."],
  ["turn_expired", "This turn is no longer available. Send the message again."],
  ["conversation_recovery_failed", "The conversation could not be recovered. Start a new conversation."],
  ["malformed_event", 'Event "delta" carried invalid JSON.'],
  ["http_403", "Chat.User"],
  ["http_500", "The API answered with HTTP 500."],
  ["network", "Could not reach the API at https://api.example."],
  ["constructor", "Not a code of ours."],
];

describe("the text for the customer", () => {
  it.each(RAW)("for %s names no backend, event, role or host", (code, raw) => {
    const text = customerText(code);
    expect(text).not.toBe(raw);
    expect(text).not.toMatch(/agent|backend|event|loop|hosted|tool|model|API|HTTP|Chat\.User|access_as_user|https?:|turn|[0-9]/i);
    expect(text).toMatch(/\.$/);
  });

  it("says what a customer can do", () => {
    expect(customerText("http_403")).toBe(
      "Runa cannot open this chat. Choose New chat and ask again. If it keeps happening, your account may not have access.",
    );
    expect(customerText("network")).toBe("Could not reach Voltrieve. Try again.");
    expect(customerText("conversation_recovery_failed")).toContain("New chat");
    expect(customerText("something_new")).toBe("Runa could not answer. Try again.");
  });

  it.each(RAW)("is in the chat for %s, and what the API said only in the timeline", (code, message) => {
    let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "hosted", correlationId: "c" });
    state = chatReducer(state, { type: "event", event: { type: "error", data: { code, message } } });
    expect(state.turns[0]!.error).toBe(customerText(code));
    expect(state.turns[0]!.entries.at(-1)).toEqual({ kind: "error", code, message });
  });
});

describe("what went wrong in a turn", () => {
  const failedToken = withSignIn(() => Promise.reject(new Error("AADSTS50058")));

  it("is the sign-in only when the token could not be had", async () => {
    const error = await failedToken().catch(errorOf);
    expect(error).toEqual({ code: "auth", message: "AADSTS50058" });
    expect(customerText("auth")).toContain("signed in");
    expect(await withSignIn(async () => "token")()).toBe("token");
  });

  it.each([new TypeError("x is undefined"), "text"])("is something else for %s", (e) => {
    const { code } = errorOf(e);
    expect(code).toBe("client");
    expect(customerText(code)).toBe("Something went wrong in the car. Reload the page and try again.");
  });
});
