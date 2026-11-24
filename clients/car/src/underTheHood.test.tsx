import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Auth } from "./auth";
import { CenterScreen } from "./components/CenterScreen";
import { Hud } from "./components/Hud";
import { Menu } from "./components/Menu";
import { readUnderTheHood, rememberUnderTheHood, UnderTheHood } from "./components/UnderTheHood";
import { readGauges } from "./gauges";
import { Pedals } from "./pedals";
import { newCar } from "./sim/car";
import { chatReducer, initialChatState, type ChatState } from "./turns";
import { Workspace } from "./Workspace";

const auth: Auth = {
  kind: "fake",
  init: async () => null,
  signIn: async () => {},
  signOut: async () => {},
  getToken: async () => "token",
};
const config = { apiUrl: "http://api", tenantId: "", clientId: "", scope: "", fakeAuth: true };
const nothing = () => {};

function memory(): Pick<Storage, "getItem" | "setItem"> {
  const values = new Map<string, string>();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => void values.set(key, value) };
}

const refusing = {
  getItem: () => {
    throw new Error("no storage");
  },
  setItem: () => {
    throw new Error("no storage");
  },
};

const page = () => renderToStaticMarkup(<Workspace config={config} auth={auth} session={{ name: "Dev", username: "dev@x" }} />);

function answered(): ChatState {
  let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "hosted", correlationId: "corr-1" });
  const trace = { step: "model_called", component: "model", detail: "iteration 1", elapsed_ms: 10 };
  state = chatReducer(state, { type: "event", event: { type: "trace", data: trace } });
  state = chatReducer(state, { type: "event", event: { type: "delta", data: { text: "Hello!" } } });
  const done = { conversation_id: "c", turn_id: "t", backend: "hosted" };
  return chatReducer(state, { type: "event", event: { type: "done", data: done } });
}

/** The text, and what a pointer or a screen reader gets besides it. */
function read(html: string): string {
  const attributes = [...html.matchAll(/\s(?:title|aria-label|alt|placeholder)="([^"]*)"/g)].map((m) => m[1]);
  return [html.replace(/<[^>]*>/g, " "), ...attributes].join(" ");
}

const HIDDEN = /\b(backend|loop|prompt|hosted|trace|event|timeline|correlation)/i;

afterEach(() => vi.unstubAllGlobals());

describe("the switch", () => {
  it("is off by default and remembered", () => {
    vi.stubGlobal("localStorage", memory());
    expect(readUnderTheHood()).toBe(false);
    rememberUnderTheHood(true);
    expect(readUnderTheHood()).toBe(true);
    rememberUnderTheHood(false);
    expect(readUnderTheHood()).toBe(false);
  });

  it("works when storage throws", () => {
    vi.stubGlobal("localStorage", refusing);
    expect(readUnderTheHood()).toBe(false);
    expect(() => rememberUnderTheHood(true)).not.toThrow();
    expect(read(page())).not.toMatch(HIDDEN);
  });

  it("is in the menu", () => {
    const menu = (on: boolean) =>
      renderToStaticMarkup(
        <Menu openAtFirst onNewChat={nothing} onSignOut={nothing} username="dev@x" underTheHood={on} onUnderTheHood={nothing} />,
      );
    expect(menu(false)).toMatch(/role="switch"[^>]*aria-checked="false"[^>]*>Under the hood/);
    expect(menu(true)).toMatch(/role="switch"[^>]*aria-checked="true"/);
    expect(read(menu(false))).not.toMatch(HIDDEN);
  });
});

describe("while the switch is off", () => {
  it("nothing names a backend or an event, in texts, titles or labels", () => {
    vi.stubGlobal("localStorage", memory());
    const html = page();
    expect(html).toContain("Talk to Runa…");
    expect(read(html)).not.toMatch(HIDDEN);
    expect(html).not.toContain("radiogroup");
  });

  it("nor does an answer, on the screen or in the HUD", () => {
    const state = answered();
    const hud = renderToStaticMarkup(<Hud open turn={state.turns.at(-1)} onEvent={nothing} onOpenSource={nothing} />);
    const screen = renderToStaticMarkup(
      <CenterScreen
        style={{}}
        state={state}
        busy={false}
        lifted={false}
        gauges={readGauges(newCar(), "off")}
        pedals={new Pedals({ speedKmh: () => 0, setSpeed: nothing })}
        field={createRef<HTMLInputElement>()}
        now={() => new Date()}
        onSetClock={nothing}
        motorOn={false}
        onMotor={nothing}
        onSend={nothing}
        onPark={nothing}
        onOpenSource={nothing}
      />,
    );
    for (const html of [hud, screen]) {
      expect(html).toContain("Hello!");
      expect(html).not.toContain("corr-1");
      expect(read(html)).not.toMatch(HIDDEN);
    }
  });
});

describe("while the switch is on", () => {
  it("the page has the timeline and the backend picker beside the car", () => {
    vi.stubGlobal("localStorage", memory());
    rememberUnderTheHood(true);
    const html = page();
    expect(html).toContain("Event timeline");
    expect(html).toContain('aria-label="Agent backend"');
    expect(html).toContain("Talk to Runa…");
  });

  it("the panel shows what happened in a turn", () => {
    const html = renderToStaticMarkup(<UnderTheHood state={answered()} backend="hosted" onBackend={nothing} busy={false} />);
    expect(html).toContain("corr-1");
    expect(html).toContain("model_called");
    expect(html).toMatch(/aria-checked="true"[^>]*>hosted/);
  });
});
