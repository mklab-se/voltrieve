import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Chat } from "./components/Chat";
import { chatReducer, initialChatState, type ChatState } from "./turns";

// Issue #15: the assistant is called Runa, and the chat never hides that it is an AI.
const render = (state: ChatState) =>
  renderToStaticMarkup(<Chat state={state} busy={false} underTheHood={false} onSend={() => {}} onOpenSource={() => {}} />);

describe("Runa in the chat", () => {
  it("labels each answer with Runa's name and avatar", () => {
    let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "loop", correlationId: "c" });
    state = chatReducer(state, { type: "event", event: { type: "delta", data: { text: "Hello!" } } });
    const html = render(state);
    expect(html).toMatch(/<p class="who"><img[^>]*src="\/runa\.png"[^>]*alt=""[^>]*\/?>Runa<\/p>/);
  });

  it("introduces Runa before the first question", () => {
    const html = render(initialChatState);
    expect(html).toContain("Ask Runa something");
    expect(html).toContain('placeholder="Ask Runa about a Voltrieve model…"');
  });

  it("says that Runa is an AI", () => {
    expect(render(initialChatState)).toContain("You are talking to Runa, an AI assistant.");
  });
});
