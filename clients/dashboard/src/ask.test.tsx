import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { Auth } from "./auth";
import { Ask, AskPage, type AskState } from "./components/Insights";
import { Gate } from "./components/Gate";
import { Header } from "./components/Header";
import type { Insights, InsightsRow, Source } from "./insights";

const getToken = vi.fn(async () => "t");
const auth: Auth = { kind: "fake", init: async () => null, signIn: async () => {}, signOut: async () => {}, getToken };

function row(over: Partial<InsightsRow>): InsightsRow {
  return { cut: "all", client: "", topic: "", car_model: "", request_kind: "", result: "", group: "", conversations: 10, people: 10, ...over };
}

function answer(source: Source, over: Partial<Insights> = {}): Insights {
  return { source, run: "2026-11-24T02:15:00Z", window: { start: "2026-11-16", end: "2026-11-22" }, sample_share: 1, rows: [], weeks: [], ...over };
}

const ROWS = [
  row({ cut: "all", people: 60, conversations: 90 }),
  row({ cut: "client", client: "car", people: 45, conversations: 70 }),
  row({ cut: "topic", topic: "charging", people: 40, conversations: 60 }),
  row({ cut: "topic_result", topic: "charging", result: "answered", conversations: 45 }),
  row({ cut: "topic_result", topic: "charging", result: "partly", conversations: 15 }),
  row({ cut: "topic", topic: "fault", people: 25, conversations: 30 }),
  row({ cut: "topic_model", topic: "charging", car_model: "fjord", people: 15 }),
  row({ cut: "group", topic: "charging", group: "<img src=x onerror=alert(1)>", people: 20 }),
  row({ cut: "group", topic: "charging", group: "long ".repeat(30), people: 10 }),
];

/** The text of the page, without the tags. */
function page(source: Source, state: AskState): { html: string; text: string } {
  const html = renderToStaticMarkup(<AskPage source={source} state={state} onSource={() => {}} onRetry={() => {}} />);
  return { html, text: html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ") };
}

const NOTE = "Only topics shared by at least five people are shown, and counts are rounded.";

describe("the tabs", () => {
  const show = (tab?: "live" | "ask") =>
    renderToStaticMarkup(
      <Header session={{ name: "A", username: "a" }} onSignOut={() => {}} theme="light" onToggleTheme={() => {}} tab={tab} onTab={() => {}} />,
    );

  it("are two, and one is chosen and in the tab order", () => {
    const html = show("ask");
    expect(html).toContain("Live");
    expect(html).toContain("What people ask");
    expect(html.match(/role="tab"/g)).toHaveLength(2);
    expect(html).toMatch(/id="tab-ask"[^>]*aria-selected="true"[^>]*tabindex="0"/i);
    expect(html).toMatch(/id="tab-live"[^>]*aria-selected="false"[^>]*tabindex="-1"/i);
  });

  it("are not there before access is allowed", () => {
    expect(show()).not.toContain('role="tab"');
  });
});

describe("the gate", () => {
  const show = (access: "checking" | "allowed" | "denied", tab: "live" | "ask") =>
    renderToStaticMarkup(<Gate access={access} apiUrl="http://api" auth={auth} tab={tab} />);

  it("has the page only with the role, so that nothing is asked for without it", () => {
    for (const access of ["checking", "denied"] as const) expect(show(access, "ask")).not.toContain(NOTE);
    expect(show("allowed", "ask")).toContain(NOTE);
  });

  it("has only one of the two, so that the live feed is not open on the other tab", () => {
    expect(show("allowed", "ask")).not.toContain("Operations dashboard");
    expect(show("allowed", "live")).toContain("Operations dashboard");
    expect(show("allowed", "live")).not.toContain(NOTE);
  });
});

describe("the page", () => {
  it("starts with real data", () => {
    expect(renderToStaticMarkup(<Ask apiUrl="http://api" auth={auth} />)).toContain("This is real use.");
  });

  it("says which data it shows, and the note, in every state", () => {
    for (const status of ["loading", "failed"] as const) {
      expect(page("real", { status }).text).toContain("This is real use.");
      expect(page("seeded", { status }).text).toContain("This is made up data, for demos.");
      expect(page("real", { status }).text).toContain(NOTE);
    }
    for (const rows of [[], ROWS]) {
      expect(page("real", { status: "ready", data: answer("real", { rows }) }).text).toContain("This is real use.");
      const made = page("seeded", { status: "ready", data: answer("seeded", { rows }) }).text;
      expect(made).toContain("This is made up data, for demos.");
      expect(made).not.toContain("This is real use.");
      expect(made).toContain(NOTE);
    }
  });

  it("never shows the answer of the other source", () => {
    const { text } = page("real", { status: "ready", data: answer("seeded", { rows: ROWS }) });
    expect(text).not.toContain("about");
  });

  it("has a switch with one of the two chosen", () => {
    const { html } = page("seeded", { status: "loading" });
    expect(html.match(/type="radio"/g)).toHaveLength(2);
    expect(html.match(/checked=""/g)).toHaveLength(1);
    expect(html).toMatch(/<input[^>]*checked=""[^>]*value="seeded"|<input[^>]*value="seeded"[^>]*checked=""/);
  });

  it("has the last run and the window, and the sample when it is one", () => {
    const { text } = page("real", { status: "ready", data: answer("real", { rows: ROWS, sample_share: 0.8 }) });
    expect(text).toContain("24 Nov 2026, 02:15 UTC");
    expect(text).toContain("16 Nov 2026 to 22 Nov 2026");
    expect(text).toContain("A sample of 80 % of the conversations");
    expect(page("real", { status: "ready", data: answer("real", { rows: ROWS }) }).text).not.toContain("A sample");
    expect(page("real", { status: "ready", data: answer("real", { run: null, window: null }) }).text).toContain("No run yet");
  });

  it("says so when there is nothing", () => {
    expect(page("real", { status: "ready", data: answer("real") }).text).toContain("Not enough people yet. Only what at least five people share is shown.");
    expect(page("seeded", { status: "ready", data: answer("seeded") }).text).toContain("No made up data yet.");
  });

  it("says so in a sentence when the request fails", () => {
    expect(page("real", { status: "failed" }).text).toContain("What people ask could not be loaded.");
  });

  const { html, text } = page("real", {
    status: "ready",
    data: answer("real", {
      rows: ROWS,
      weeks: [
        { end: "2026-11-22", topic: "charging", people: 40, conversations: 60 },
        { end: "2026-11-15", topic: "charging", people: 35, conversations: 50 },
        { end: "2026-11-01", topic: "charging", people: 30, conversations: 45 },
      ],
    }),
  });

  it("has about in front of the counts", () => {
    expect(text).toMatch(/about 60 people/);
    expect(text).toMatch(/about 90 conversations/);
    expect(text).toMatch(/about 40 people, about 60 conversations/);
  });

  it("has the share answered of the answers counted, only where results are saved", () => {
    expect(text.match(/about 75 % of the answers counted/g)).toHaveLength(1);
    expect(text.match(/% of the answers counted/g)).toHaveLength(1);
  });

  it("has clients and car models with friendly labels", () => {
    expect(text).toContain("Car");
    expect(text).toContain("Fjord");
    expect(text).toContain("Charging");
    expect(text).toContain("about 15");
  });

  it("shows a group as text, cut at 80 characters with all of it as title", () => {
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    const long = "long ".repeat(30);
    expect(html).toContain(`title="${long}"`);
    expect(html.split(long)).toHaveLength(2);
  });

  it("has a line chart with words and a table, and no line across a missing week", () => {
    expect(html).toMatch(/<svg[^>]*role="img"[^>]*aria-label="Charging[^"]*about 40/);
    expect(html.match(/<polyline/g)).toHaveLength(1);
    expect(html).toContain("Show the weeks as a table");
  });
});
