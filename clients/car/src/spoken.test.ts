import { describe, expect, it } from "vitest";
import { plainText, spoken } from "./spoken";
import { chatReducer, initialChatState, type ChatAction, type ChatState } from "./turns";

const start: ChatAction = { type: "start", message: "Hi", backend: "loop", correlationId: "c" };
const chat = (...actions: ChatAction[]): ChatState => actions.reduce(chatReducer, chatReducer(initialChatState, start));
const delta = (text: string): ChatAction => ({ type: "event", event: { type: "delta", data: { text } } });
const done: ChatAction = { type: "event", event: { type: "done", data: { conversation_id: "c", turn_id: "t", backend: "loop" } } };

describe("text without Markdown", () => {
  it.each([
    ["Yes, for **2 hours**.", "Yes, for 2 hours."],
    ["*maybe* and __sure__ and _so_", "maybe and sure and so"],
    ["## Range\n\n- one\n- two", "Range\none\ntwo"],
    ["1. first\n2. second", "first\nsecond"],
    ["See [the manual](https://x.example/a).", "See the manual."],
    ["![a sign](https://x.example/a.png) Here", "a sign Here"],
    ["Use `set_speed` now", "Use set_speed now"],
    ["> quoted", "quoted"],
    ["| Model | Price |\n|---|---:|\n| Spark | 1 |", "Model, Price\nSpark, 1"],
    ["set_speed and 3 * 4 * 5", "set_speed and 3 * 4 * 5"],
  ])("%j is %j", (markdown, text) => expect(plainText(markdown)).toBe(text));
});

describe("what a screen reader says", () => {
  it("is nothing before the first message", () => expect(spoken(initialChatState)).toBe(""));

  it("is what Runa is doing, and not each word of the answer", () => {
    expect(spoken(chat())).toBe("Runa is thinking");
    expect(spoken(chat(), "Parking the car")).toBe("Parking the car");
    expect(spoken(chat(delta("**Yes")))).toBe("Runa is thinking");
  });

  it("is the whole answer at the end, in plain text", () => {
    expect(spoken(chat(delta("**Yes**, for 2 hours."), done))).toBe("Runa: Yes, for 2 hours.");
  });
});
