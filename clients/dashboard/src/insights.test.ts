import { afterEach, describe, expect, it, vi } from "vitest";
import {
  about, answeredShare, askView, buildInsightsRequest, cut, day, label, loadInsights, nextTab, runs, sampleNote, tabFromHash, trends,
  type Insights, type InsightsRow,
} from "./insights";

function row(over: Partial<InsightsRow>): InsightsRow {
  return { cut: "all", client: "", topic: "", car_model: "", request_kind: "", result: "", group: "", conversations: 10, people: 10, ...over };
}

function answer(over: Partial<Insights> = {}): Insights {
  return {
    source: "real", run: "2026-11-24T02:15:00Z", window: { start: "2026-11-16", end: "2026-11-22" }, sample_share: 1, rows: [], weeks: [], ...over,
  };
}

const auth = { getToken: async () => "tok" };

describe("the tab in the address", () => {
  it("is Live unless the hash is #ask", () => {
    expect(tabFromHash("#ask")).toBe("ask");
    expect(["", "#", "#live", "#ask2", "#code=abc"].map(tabFromHash)).toEqual(["live", "live", "live", "live", "live"]);
  });

  it("moves with the arrow keys, round the ends, and with Home and End", () => {
    expect(nextTab("live", "ArrowRight")).toBe("ask");
    expect(nextTab("ask", "ArrowRight")).toBe("live");
    expect(nextTab("live", "ArrowLeft")).toBe("ask");
    expect(nextTab("ask", "Home")).toBe("live");
    expect(nextTab("live", "End")).toBe("ask");
    expect(nextTab("live", "Enter")).toBeNull();
  });
});

describe("the request", () => {
  afterEach(() => void vi.unstubAllGlobals());

  it("names the source and sends the bearer token", () => {
    const { url, init } = buildInsightsRequest("http://api/", "tok", "seeded");
    expect(url).toBe("http://api/admin/insights?source=seeded");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok" });
  });

  it("gives the answer for the source that was asked for", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(answer())));
    expect(await loadInsights("http://api", auth, "real", new AbortController().signal)).toEqual(answer());
  });

  it("fails on an error, on another source and on something else", async () => {
    const load = (body: unknown, status = 200) => {
      vi.stubGlobal("fetch", async () => new Response(JSON.stringify(body), { status }));
      return loadInsights("http://api", auth, "real", new AbortController().signal);
    };
    await expect(load({ detail: "secret" }, 503)).rejects.toThrow();
    await expect(load(answer({ source: "seeded" }))).rejects.toThrow();
    await expect(load({ source: "real" })).rejects.toThrow();
    await expect(load(null)).rejects.toThrow();
  });

  it("leaves out a row without a number, so that one bad row does not take the page down", async () => {
    const bad = { ...row({ cut: "client" }), people: null };
    const week = { end: "2026-11-22", topic: "fault", people: null, conversations: 5 };
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ ...answer(), rows: [bad, row({})], weeks: [week] })));
    const got = await loadInsights("http://api", auth, "real", new AbortController().signal);
    expect(got.rows).toEqual([row({})]);
    expect(got.weeks).toEqual([]);
  });

  it("is not sent when the page is left while the token is fetched", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const ctrl = new AbortController();
    const slow = { getToken: async () => { ctrl.abort(); return "tok"; } };
    await expect(loadInsights("http://api", slow, "real", ctrl.signal)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("words", () => {
  it("has about in front of a count", () => {
    expect(about(10)).toBe("about 10");
    expect(about(1200)).toBe("about 1,200");
  });

  it("has a friendly label, and shows an unknown one as it is", () => {
    expect(label("topic", "range_battery")).toBe("Range and battery");
    expect(label("result", "not_found")).toBe("Not found");
    expect(label("kind", "how_to")).toBe("How to");
    expect(label("model", "fjord")).toBe("Fjord");
    expect(label("client", "web")).toBe("Website");
    expect(label("group", "unnamed group")).toBe("Unnamed group");
    expect(label("topic", "brand_new")).toBe("brand_new");
    expect(label("group", "constructor")).toBe("constructor");
  });

  it("cuts at 80 characters", () => {
    expect(cut("a".repeat(80))).toBe("a".repeat(80));
    expect([...cut("a".repeat(81))]).toHaveLength(80);
    expect(cut("a".repeat(81)).endsWith("…")).toBe(true);
    expect([...cut("🔋".repeat(81))]).toHaveLength(80);
  });

  it("reads a day in UTC, with or without a time", () => {
    expect(day("2026-11-22")).toBe("22 Nov 2026");
    expect(day("2026-11-22T00:00:00Z")).toBe("22 Nov 2026");
  });

  it("names the sample only when it is one, and never rounds it up to all", () => {
    expect(sampleNote(0.8)).toBe("A sample of 80 % of the conversations");
    expect(sampleNote(0.29)).toBe("A sample of 29 % of the conversations");
    expect(sampleNote(0.999)).toBe("A sample of 99 % of the conversations");
    expect(sampleNote(1)).toBeNull();
    expect(sampleNote(null)).toBeNull();
  });
});

describe("the share answered", () => {
  const results = (counts: Record<string, number>) =>
    Object.entries(counts).map(([result, conversations]) => row({ cut: "topic_result", result, conversations }));

  it("is answered divided by the sum of the saved results", () => {
    expect(answeredShare(results({ answered: 30, partly: 5, not_found: 5 }))).toBe(75);
  });

  it("is nothing without saved results, or without a row for answered", () => {
    expect(answeredShare([])).toBeNull();
    expect(answeredShare(results({ partly: 10 }))).toBeNull();
    expect(answeredShare(results({ answered: 0 }))).toBeNull();
  });
});

describe("what the page shows", () => {
  const view = askView(answer({
    rows: [
      row({ cut: "all", people: 60, conversations: 90 }),
      row({ cut: "client", client: "web", people: 15 }),
      row({ cut: "client", client: "car", people: 45 }),
      row({ cut: "topic", topic: "other", people: 50 }),
      row({ cut: "topic", topic: "fault", people: 20, conversations: 25 }),
      row({ cut: "topic", topic: "charging", people: 40, conversations: 60 }),
      row({ cut: "topic_result", topic: "charging", result: "answered", conversations: 45 }),
      row({ cut: "topic_result", topic: "charging", result: "partly", conversations: 15 }),
      row({ cut: "topic_kind", topic: "charging", request_kind: "how_to", people: 30 }),
      row({ cut: "topic_model", topic: "charging", car_model: "fjord", people: 10 }),
      row({ cut: "topic_model", topic: "fault", car_model: "spark", people: 20 }),
      row({ cut: "group", topic: "charging", group: "other", people: 30 }),
      row({ cut: "group", topic: "charging", group: "Charging at home", people: 20 }),
      row({ cut: "group", topic: "fault", group: "Warning lights", people: 10 }),
      row({ cut: "group_result", topic: "charging", group: "Charging at home", result: "answered", conversations: 10 }),
      row({ cut: "group_result", topic: "charging", group: "Charging at home", result: "refused", conversations: 10 }),
    ],
  }));

  it("takes the totals from the all row and never adds parts up", () => {
    expect(view.total).toMatchObject({ people: 60, conversations: 90 });
    expect(askView(answer()).total).toBeUndefined();
  });

  it("puts the most people first, and other last", () => {
    expect(view.topics.map((t) => t.topic)).toEqual(["charging", "fault", "other"]);
    expect(view.clients.map((c) => c.client)).toEqual(["car", "web"]);
    expect(view.topics[0].groups.map((g) => g.name)).toEqual(["Charging at home", "other"]);
  });

  it("gives a topic its own groups, results and kinds only", () => {
    expect(view.topics[0]).toMatchObject({ answered: 75, people: 40, conversations: 60 });
    expect(view.topics[0].groups[0].answered).toBe(50);
    expect(view.topics[0].kinds.map((k) => k.request_kind)).toEqual(["how_to"]);
    expect(view.topics[1]).toMatchObject({ answered: null, groups: [{ name: "Warning lights", answered: null }] });
  });

  it("has people per topic and car model, with nothing where no row is saved", () => {
    expect(view.models).toEqual(["fjord", "spark"]);
    expect(view.topics[0].models).toEqual({ fjord: 10 });
    expect(view.topics[1].models).toEqual({ spark: 20 });
  });
});

describe("the weeks", () => {
  const week = (end: string, topic: string, people: number) => ({ end, topic, people, conversations: people });

  it("are twelve, oldest first, ending with the window, and a missing one is no number", () => {
    const t = trends(answer({ weeks: [week("2026-11-22", "charging", 20), week("2026-11-08", "charging", 10), week("2026-09-06", "fault", 5)] }));
    expect(t.ends).toHaveLength(12);
    expect(t.ends[0]).toBe("2026-09-06");
    expect(t.ends[11]).toBe("2026-11-22");
    expect(t.series.find((s) => s.topic === "charging")!.points.slice(-3)).toEqual([10, null, 20]);
    expect(t.series.find((s) => s.topic === "fault")!.points[0]).toBe(5);
    expect(t.max).toBe(20);
  });

  it("keeps the topics in the order of the list", () => {
    const rows = [row({ cut: "topic", topic: "fault", people: 30 }), row({ cut: "topic", topic: "charging", people: 5 })];
    const t = trends(answer({ rows, weeks: [week("2026-11-22", "charging", 5), week("2026-11-22", "fault", 30), week("2026-11-15", "range_battery", 5)] }));
    expect(t.series.map((s) => s.topic)).toEqual(["fault", "charging", "range_battery"]);
  });

  it("takes one night per week when there is one a night, and the same night for every topic", () => {
    const t = trends(answer({ weeks: [week("2026-11-22", "charging", 20), week("2026-11-21", "charging", 45), week("2026-11-21", "fault", 10)] }));
    expect(t.series.find((s) => s.topic === "charging")!.points.at(-1)).toBe(20);
    expect(t.series.find((s) => s.topic === "fault")?.points.at(-1) ?? null).toBeNull();
  });

  it("leaves out what is older than twelve weeks or not a day, and is empty without any", () => {
    expect(trends(answer({ weeks: [week("2026-08-01", "charging", 20), week("soon", "charging", 20)] })).series).toEqual([]);
    expect(trends(answer({ window: null })).series).toEqual([]);
  });

  it("are split where a week is missing, so that no line crosses it", () => {
    expect(runs([5, 10, null, null, 15, null])).toEqual([[[0, 5], [1, 10]], [[4, 15]]]);
    expect(runs([null])).toEqual([]);
  });
});
