import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Workspace } from "./App";
import type { Auth } from "./auth";
import { Chat } from "./components/Chat";
import { SignIn } from "./components/Notices";
import { UnderTheHood, readUnderTheHood, rememberUnderTheHood } from "./components/UnderTheHood";
import { chatReducer, initialChatState, type ChatState } from "./turns";

const auth: Auth = {
  kind: "fake",
  init: async () => null,
  signIn: async () => {},
  signOut: async () => {},
  getToken: async () => "token",
};
const config = { apiUrl: "http://api", tenantId: "", clientId: "", scope: "", fakeAuth: true };

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

function page(): string {
  vi.stubGlobal("document", { documentElement: { dataset: {} } });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  return renderToStaticMarkup(<Workspace config={config} auth={auth} session={{ name: "Dev", username: "dev@x" }} />);
}

function answered(): ChatState {
  let state = chatReducer(initialChatState, { type: "start", message: "Hi", backend: "loop", correlationId: "corr-1" });
  state = chatReducer(state, { type: "event", event: { type: "delta", data: { text: "Hello!" } } });
  const done = { conversation_id: "c", turn_id: "t", backend: "loop" };
  return chatReducer(state, { type: "event", event: { type: "done", data: done } });
}

const chat = (underTheHood: boolean) =>
  renderToStaticMarkup(
    <Chat state={answered()} busy={false} underTheHood={underTheHood} onSend={() => {}} onOpenSource={() => {}} />,
  );

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
    expect(page()).toMatch(/role="switch"[^>]*aria-checked="false"/);
  });
});

describe("while the switch is off", () => {
  it("the page has no timeline, no backend picker and no tab for the timeline", () => {
    vi.stubGlobal("localStorage", memory());
    const html = page();
    expect(html).toMatch(/role="switch"[^>]*aria-checked="false"/);
    expect(html).not.toMatch(/timeline/i);
    expect(html).not.toContain("radiogroup");
    expect(html).not.toContain("hosted");
    expect(html).not.toContain("correlation id");
    expect(html).not.toContain('class="tabs"');
  });

  it("an answer names neither the backend nor the correlation id", () => {
    const html = chat(false);
    expect(html).toContain("Hello!");
    expect(html).not.toContain("loop");
    expect(html).not.toContain("corr-1");
  });
});

describe("while the switch is on", () => {
  it("the page has the timeline with its tab and the backend picker", () => {
    const storage = memory();
    vi.stubGlobal("localStorage", storage);
    rememberUnderTheHood(true);
    const html = page();
    expect(html).toMatch(/role="switch"[^>]*aria-checked="true"/);
    expect(html).toContain("Event timeline");
    expect(html).toContain('aria-label="Agent backend"');
    expect(html).toContain('class="tabs"');
  });

  it("the panel shows the correlation id, and the answer its backend", () => {
    const html = renderToStaticMarkup(<UnderTheHood state={answered()} backend="loop" onBackend={() => {}} busy={false} />);
    expect(html).toContain("corr-1");
    expect(html.indexOf("Agent backend")).toBeGreaterThan(-1);
    expect(chat(true)).toContain("via loop");
  });
});

describe("the customer page", () => {
  it("is called My Voltrieve", () => {
    vi.stubGlobal("localStorage", memory());
    expect(page()).toContain('<span class="brand-name">My Voltrieve</span>');
  });

  it("welcomes the customer before sign-in", () => {
    const html = renderToStaticMarkup(<SignIn auth={auth} />);
    expect(html).toContain("<h1>Welcome to My Voltrieve</h1>");
    expect(html).toContain("<p>Sign in to see your car and ask Runa about it.</p>");
  });
});
