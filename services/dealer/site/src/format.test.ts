import { describe, expect, it } from "vitest";
import { clientName, day, money, place, when } from "./format";

describe("money", () => {
  it("has a space between thousands", () => {
    expect(money(529000).replace(/ /g, " ")).toBe("529 000 SEK");
    expect(money(1234567).replace(/ /g, " ")).toBe("1 234 567 SEK");
    expect(money(900).replace(/ /g, " ")).toBe("900 SEK");
  });
});

describe("a time", () => {
  const now = new Date(2026, 10, 24, 15, 0);
  it("is the hour today, and has the day before that", () => {
    expect(when(new Date(2026, 10, 24, 9, 5).toISOString(), now)).toBe("09:05");
    expect(when(new Date(2026, 10, 3, 9, 5).toISOString(), now)).toBe("3 Nov 09:05");
    expect(when("not a time", now)).toBe("");
  });
});

describe("a day", () => {
  it("is written out", () => {
    expect(day(new Date(2026, 10, 3, 9, 5).toISOString())).toBe("3 November 2026");
    expect(day("not a time")).toBe("");
  });
});

describe("where an order came from", () => {
  it("is in plain words", () => {
    expect([clientName("web"), clientName("cli"), clientName("car"), clientName("new")]).toEqual(["website", "command line", "car", "new"]);
    expect([place("web"), place("cli"), place("new")]).toEqual(["on the website", "in the command line", "from new"]);
  });
});
