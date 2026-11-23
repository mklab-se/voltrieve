import { describe, expect, it } from "vitest";
import { buildLiveRequest, canViewDashboard, feedReducer, initialFeed, stats, type FeedTurn } from "./dashboard";

function token(payload: object): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function turn(over: Partial<FeedTurn>): FeedTurn {
  return {
    turn_id: "t", user: "u", backend: "loop", prompt: null, outcome: "answered", at: 1,
    first_text_ms: 1000, duration_ms: 2000, tools: [], knowledge_bases: [], cost_sek: 0.01, ...over,
  };
}

describe("canViewDashboard", () => {
  it("needs the Dashboard.View role in the access token", () => {
    expect(canViewDashboard(token({ roles: ["Chat.User", "Dashboard.View"] }))).toBe(true);
    expect(canViewDashboard(token({ roles: ["Chat.User"] }))).toBe(false);
    expect(canViewDashboard("not-a-token")).toBe(false);
  });
});

describe("feedReducer", () => {
  it("merges updates of a turn and keeps the newest first", () => {
    let s = feedReducer(initialFeed, { event: "turn", data: turn({ turn_id: "a", outcome: "running", duration_ms: undefined }) });
    s = feedReducer(s, { event: "turn", data: turn({ turn_id: "b", at: 2 }) });
    s = feedReducer(s, { event: "turn", data: { turn_id: "a", outcome: "answered", duration_ms: 3000 } as FeedTurn });
    expect(s.turns.map((t) => t.turn_id)).toEqual(["b", "a"]);
    expect(s.turns[1]).toMatchObject({ outcome: "answered", duration_ms: 3000, backend: "loop" });
  });

  it("takes the summary and caps the feed", () => {
    let s = feedReducer(initialFeed, { event: "summary", data: { active_users: 3, turns: 9 } });
    for (let n = 0; n < 60; n += 1) s = feedReducer(s, { event: "turn", data: turn({ turn_id: `t${n}`, at: n }) });
    expect(s.summary).toEqual({ active_users: 3, turns: 9 });
    expect(s.turns).toHaveLength(50);
    expect(s.turns[0].turn_id).toBe("t59");
  });
});

describe("feedReducer on reconnect", () => {
  it("starts over on reset and orders turns by time, newest first", () => {
    let s = feedReducer(initialFeed, { event: "turn", data: turn({ turn_id: "old" }) });
    s = feedReducer(s, { event: "reset" });
    expect(s.turns).toEqual([]);
    s = feedReducer(s, { event: "turn", data: turn({ turn_id: "late", at: 20 }) });
    s = feedReducer(s, { event: "turn", data: turn({ turn_id: "early", at: 10 }) });
    expect(s.turns.map((t) => t.turn_id)).toEqual(["late", "early"]);
  });
});

describe("stats", () => {
  it("gives median first text per backend, tool and knowledge base counts and the cost", () => {
    const s = stats([
      turn({ turn_id: "1", backend: "loop", first_text_ms: 1000, tools: ["search_spare_parts"], knowledge_bases: ["voltrieve-parts-kb"] }),
      turn({ turn_id: "2", backend: "loop", first_text_ms: 3000, tools: ["search_spare_parts", "check_part_stock"] }),
      turn({ turn_id: "3", backend: "hosted", first_text_ms: 8000, knowledge_bases: ["voltrieve-kb"] }),
      turn({ turn_id: "4", backend: "hosted", outcome: "running", first_text_ms: null }),
    ]);
    expect(s.medianFirstText).toEqual({ loop: 2000, hosted: 8000 });
    expect(s.tools).toEqual([["search_spare_parts", 2], ["check_part_stock", 1]]);
    expect(s.knowledgeBases).toEqual([["voltrieve-parts-kb", 1], ["voltrieve-kb", 1]]);
    expect(s.costSek).toBeCloseTo(0.04);
    expect(s.running).toBe(1);
  });

  it("keeps the backends in one order, whichever answered last", () => {
    const order = (...backends: string[]) =>
      Object.keys(stats(backends.map((backend, n) => turn({ turn_id: `${n}`, backend, first_text_ms: 1 }))).medianFirstText);
    expect(order("hosted", "prompt", "loop")).toEqual(["loop", "prompt", "hosted"]);
    expect(order("new", "prompt", "hosted")).toEqual(["prompt", "hosted", "new"]);
  });
});

describe("buildLiveRequest", () => {
  it("asks for prompts only when the switch is on", () => {
    expect(buildLiveRequest("https://api/", "tok", false).url).toBe("https://api/admin/live");
    const on = buildLiveRequest("https://api", "tok", true);
    expect(on.url).toBe("https://api/admin/live?prompts=true");
    expect((on.init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });
});
