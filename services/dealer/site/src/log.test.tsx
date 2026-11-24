import { describe, expect, it, vi } from "vitest";
import { dealerState, entry } from "./testing/sample";
import { show } from "./testing/react";

vi.mock("react", async () => (await import("./testing/react")).mocked);
const { Log } = await import("./components/Log");
const { sentence } = await import("./sentence");

const names = { fjord: "Fjord" };
const say = (over: Parameters<typeof entry>[1]) => sentence(entry(1, over), names);

describe("each kind has a sentence", () => {
  it.each([
    [{ kind: "stock", model: "fjord", city: "Göteborg" }, "Looked up the stock of Fjord in Göteborg."],
    [{ kind: "stock", model: "fjord" }, "Looked up the stock of Fjord."],
    [{ kind: "stock" }, "Looked up the stock."],
    [{ kind: "part_stock", city: "Malmö" }, "Looked up spare parts in Malmö."],
    [{ kind: "prices" }, "Looked up the prices."],
    [{ kind: "terms", model: "fjord" }, "Looked up the terms for Fjord."],
    [{ kind: "offer", model: "fjord" }, "Asked for an offer for Fjord."],
    [{ kind: "order", model: "fjord", number: "VO-1002" }, "Placed order VO-1002 for Fjord."],
    [{ kind: "order", model: "fjord" }, "Asked to order Fjord."],
    [{ kind: "test_drive", model: "fjord", city: "Malmö" }, "Asked for a test drive of Fjord in Malmö."],
    [{ kind: "delivered", model: "fjord", number: "VO-1002", city: "Malmö" }, "Delivered VO-1002, Fjord, from Malmö."],
    [{ kind: "deleted", number: "VO-1002" }, "Deleted order VO-1002."],
    [{ kind: "stock_changed", model: "fjord", city: "Malmö" }, "Changed the stock of Fjord in Malmö."],
    [{ kind: "reset" }, "Reset the demo."],
    [{ kind: "refused" }, "Was refused: no access."],
  ])("%o", (over, text) => {
    expect(say(over)).toBe(text);
  });

  it("with the result where there is one", () => {
    expect(say({ kind: "stock", model: "fjord", city: "Malmö", result: "3" })).toBe("Looked up the stock of Fjord in Malmö: 3.");
    expect(say({ kind: "stock", result: 0 as unknown as string })).toBe("Looked up the stock: 0.");
    expect(say({ kind: "order", model: "fjord", result: "The offer has expired." })).toBe("Asked to order Fjord: The offer has expired.");
  });

  it("also one that this page does not know", () => {
    expect(say({ kind: "new_kind" })).toBe("Did something this page does not know.");
    expect(say({ kind: "constructor" })).toBe("Did something this page does not know.");
  });
});

describe("the log", () => {
  it("has the newest on top, and says who it was and from where", () => {
    const log = [entry(1, { kind: "prices" }), entry(2, { kind: "reset", who: "Anna Rep" }), entry(3, { kind: "offer", model: "fjord", client: "web" })];
    const p = show(<Log state={dealerState({ log })} />);
    const rows = p.all("li").map((li) => p.text(li.children));
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain("Asked for an offer for Fjord.");
    expect(rows[0]).toContain("Runa, website");
    expect(rows[1]).toContain("Anna Rep");
    expect(rows[2]).toContain("Looked up the prices.");
  });

  it("says when it is empty", () => {
    expect(show(<Log state={dealerState()} />).text()).toContain("Nothing yet.");
  });

  it("takes an entry without a name", () => {
    const p = show(<Log state={dealerState({ log: [entry(1, { kind: "refused", who: null })] })} />);
    expect(p.text()).toContain("Unknown caller");
  });
});
