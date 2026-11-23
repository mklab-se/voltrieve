import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Chat } from "./components/Chat";
import { chatReducer, initialChatState, type ChatState } from "./turns";

const photo = { attached: {}, choose: () => {}, retry: () => {}, remove: () => {} };

// Issue #15: the assistant is called Runa, and the chat never hides that it is an AI.
const render = (state: ChatState) =>
  renderToStaticMarkup(<Chat state={state} busy={false} underTheHood={false} photo={photo} onDraft={() => {}} onSend={() => {}} onOpenSource={() => {}} />);

describe("Runa in the chat", () => {
  it("labels each answer with Runa's name and avatar", () => {
    let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "loop", correlationId: "c" });
    state = chatReducer(state, { type: "event", event: { type: "delta", data: { text: "Hello!" } } });
    const html = render(state);
    expect(html).toMatch(/<p class="who"><img[^>]*src="\/runa\.png"[^>]*alt=""[^>]*\/?>Runa<\/p>/);
  });

  it("introduces Runa before the first question", () => {
    const html = render(initialChatState);
    expect(html).toContain("Ask Runa about your car");
    expect(html).toContain('placeholder="Ask Runa about your car…"');
  });

  it("suggests questions about the customer's own car", () => {
    const html = render(initialChatState);
    const questions = [...html.matchAll(/<button class="chip">([^<]+)<\/button>/g)].map((m) => m[1]);
    expect(questions).toEqual([
      "How fast can I charge at home?",
      "When is my next service and what is done?",
      "Which winter wheels fit my car?",
      "What does my warranty cover?",
    ]);
  });

  it("keeps the title over a conversation", () => {
    const state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "loop", correlationId: "c" });
    expect(render(state).match(/Ask Runa about your car<\/h2>/g)).toHaveLength(1);
    expect(render(initialChatState).match(/Ask Runa about your car<\/h2>/g)).toHaveLength(1);
  });

  it("says that Runa is an AI", () => {
    expect(render(initialChatState)).toContain("You are talking to Runa, an AI assistant.");
  });
});
