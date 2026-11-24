import { afterEach, describe, expect, it, vi } from "vitest";
import { dealerState, fakeApi, settle } from "./testing/sample";
import { show } from "./testing/react";

vi.mock("react", async () => (await import("./testing/react")).mocked);
const { Inventory } = await import("./components/Inventory");
const { Desk } = await import("./App");

const stops: (() => void)[] = [];
afterEach(() => {
  stops.splice(0).forEach((s) => s());
});

describe("the inventory", () => {
  it("has a minus that is off at 0, and a plus that is on", () => {
    const onChange = vi.fn();
    const p = show(<Inventory state={dealerState()} flashes={{}} onChange={onChange} />);
    expect(p.button("One Spark less in Uppsala").props.disabled).toBe(true);
    expect(p.button("One Spark more in Uppsala").props.disabled).toBeFalsy();
    expect(p.button("One Spark less in Malmö").props.disabled).toBe(false);
    p.click("One Spark more in Uppsala");
    expect(onChange).toHaveBeenCalledExactlyOnceWith("spark", "Uppsala", 1);
  });

  it("has a name for every column", () => {
    const p = show(<Inventory state={dealerState()} flashes={{}} onChange={() => {}} />);
    expect(p.all("thead").flatMap((h) => p.all("th", h.children)).map((th) => p.name(th))).toEqual(["Model", "Stockholm", "Göteborg", "Malmö", "Uppsala"]);
    expect(p.all("thead").flatMap((h) => p.all("td", h.children))).toEqual([]);
  });

  it("flashes a number that changed", () => {
    const p = show(<Inventory state={dealerState()} flashes={{ "spark Malmö": 2 }} onChange={() => {}} />);
    expect(p.all("span").filter((s) => String(s.props.className).includes("flash")).map((s) => p.text(s.children))).toEqual(["5"]);
  });
});

describe("the page", () => {
  async function page() {
    const f = fakeApi();
    const p = show(<Desk api={f.api} session={{ name: "Anna Rep", username: "anna@voltrieve.example" }} theme="light" onToggleTheme={() => {}} onSignOut={() => {}} />);
    stops.push(p.unmount);
    await settle();
    f.reads[0].resolve(dealerState());
    await settle();
    const count = () => p.text(p.all("td").filter((td) => p.all("button", td.children).some((b) => p.name(b) === "One Spark more in Uppsala"))[0].children).replace(/[^0-9]/g, "");
    return { ...f, ...p, count };
  }

  it("sends one change for one click on plus, and shows it at once", async () => {
    const p = await page();
    p.click("One Spark more in Uppsala");
    expect(p.count()).toBe("1");
    expect(p.writes.map((w) => w.call)).toEqual([["changeStock", "spark", "Uppsala", 1]]);
  });

  it("puts the number back and shows the notice when the change fails", async () => {
    const p = await page();
    p.click("One Spark more in Uppsala");
    p.writes[0].reject(p.error(503, "The dealer system is not available right now."));
    await settle();
    expect(p.count()).toBe("0");
    expect(p.text()).toContain("The dealer system is not available right now.");
    p.click("Close the notice");
    expect(p.text()).not.toContain("The dealer system is not available right now.");
  });

  it("says in the header that the connection is live, and that it is lost", async () => {
    const p = await page();
    expect(p.text()).toContain("Connecting");
    p.streams[0].push({ event: "open" });
    await settle();
    expect(p.text()).toContain("Live");
    p.streams[0].end();
    await settle();
    expect(p.text()).toContain("Connection lost. Trying again.");
  });

  it("asks before it resets the demo", async () => {
    const p = await page();
    p.click("Reset the demo");
    expect(p.writes).toEqual([]);
    expect(p.text()).toContain("Reset the demo?");
    p.click("Keep everything");
    expect(p.writes).toEqual([]);
    p.click("Reset the demo");
    p.click("Reset it");
    expect(p.writes.map((w) => w.call)).toEqual([["reset"]]);
  });
});
