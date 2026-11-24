import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CenterScreen } from "./components/CenterScreen";
import { Hud } from "./components/Hud";
import { readGauges } from "./gauges";
import { Pedals } from "./pedals";
import { newCar } from "./sim/car";
import { chatReducer, initialChatState, type ChatState } from "./turns";

// Issue #15: the assistant is called Runa, and the car never hides that it is an AI.
const nothing = () => {};

const screen = (state: ChatState) =>
  renderToStaticMarkup(
    <CenterScreen
      style={{}}
      state={state}
      busy={false}
      lifted={false}
      gauges={readGauges(newCar(), "off")}
      pedals={new Pedals({ speedKmh: () => 0, setSpeed: nothing })}
      field={createRef<HTMLInputElement>()}
      now={() => new Date(2026, 1, 3, 9, 5)}
      onSetClock={nothing}
      motorOn={false}
      onMotor={nothing}
      onSend={nothing}
      onPark={nothing}
      onOpenSource={nothing}
    />,
  );

const start = chatReducer(initialChatState, { type: "start", message: "Hi there", backend: "loop", correlationId: "c" });
const event = (state: ChatState, e: Parameters<typeof chatReducer>[1] & { type: "event" }) => chatReducer(state, e);
const hud = (state: ChatState) =>
  renderToStaticMarkup(<Hud open turn={state.turns.at(-1)} onEvent={nothing} onOpenSource={nothing} />);

describe("Runa on the center screen", () => {
  it("says that Runa is an AI", () => {
    expect(screen(initialChatState)).toContain("I am Runa, an AI assistant.");
    expect(screen(initialChatState)).toContain('placeholder="Talk to Runa…"');
  });

  it("shows the conversation", () => {
    const state = event(start, { type: "event", event: { type: "delta", data: { text: "**Hello!**" } } });
    expect(screen(state)).toContain("Hi there");
    expect(screen(state)).toContain("<strong>Hello!</strong>");
  });

  it("shows the clock of the car, and has no sign to ask about yet", () => {
    const html = screen(initialChatState);
    expect(html).toContain("09:05");
    expect(html).toMatch(/<button[^>]*class="cs-park"[^>]*disabled=""[^>]*>Park here\?/);
  });
});

describe("Runa in the HUD", () => {
  it("shows Runa's name, what the driver said, and that Runa is thinking", () => {
    const html = hud(start);
    expect(html).toMatch(/src="\/runa\.png"[^>]*\/?><span>Runa<\/span>/);
    expect(html).toContain("Hi there");
    expect(html).toContain("Thinking");
  });

  it("shows what Runa is doing while there is no answer yet", () => {
    const state = event(start, { type: "event", event: { type: "status", data: { code: "x", message: "Reading the sign" } } });
    expect(hud(state)).toContain("Reading the sign");
  });

  it("shows the question before the photo is sent", () => {
    const html = renderToStaticMarkup(<Hud open pending="Can I park here?" onEvent={nothing} onOpenSource={nothing} />);
    expect(html).toContain("Can I park here?");
    expect(html).toContain("Looking at the sign");
  });

  it("never says she", () => {
    for (const html of [screen(initialChatState), screen(start), hud(start)]) expect(html).not.toMatch(/\b(she|her)\b/i);
  });
});
