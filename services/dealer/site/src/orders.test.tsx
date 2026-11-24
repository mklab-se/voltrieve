import { afterEach, describe, expect, it, vi } from "vitest";
import { dealerState, order } from "./testing/sample";
import { show } from "./testing/react";

vi.mock("react", async () => (await import("./testing/react")).mocked);
const { Orders } = await import("./components/Orders");

function page(over: Parameters<typeof dealerState>[0] = {}, busy: string[] = []) {
  const actions = { onDeliver: vi.fn(async () => true), onDelete: vi.fn(async () => true) };
  const view = (next: typeof over, b = busy) => <Orders state={dealerState(next)} busy={new Set(b)} {...actions} />;
  return { ...show(view(over)), view, ...actions };
}
afterEach(() => vi.restoreAllMocks());

describe("an open order", () => {
  it("shows what was ordered, by whom, from where", () => {
    const text = page({ orders: [order("VO-1002")] }).text().replace(/ /g, " ");
    for (const part of ["VO-1002", "Fjord", "529 000 SEK", "Kim Berg", "Storgatan 1, Uppsala", "on the website", "1 open"]) expect(text).toContain(part);
  });

  it("cannot be delivered with none in stock, and can when a car is added", () => {
    const p = page({ orders: [order("VO-1002")] });
    expect(p.button("None in stock").props.disabled).toBe(true);
    expect(p.has("Deliver VO-1002")).toBe(false);
    const stock = dealerState().stock;
    stock.fjord.Malmö = 1;
    p.update(p.view({ orders: [order("VO-1002")], stock }));
    expect(p.has("None in stock")).toBe(false);
    p.click("Deliver VO-1002");
    expect(p.onDeliver).toHaveBeenCalledExactlyOnceWith("VO-1002", "Malmö");
  });

  it("starts on the city with the most, and keeps the one that was chosen", () => {
    const o = { orders: [order("VO-1002", { model: "spark" })] };
    const p = page(o);
    expect(p.all("option").map((e) => p.name(e))).toEqual(["Malmö (5)", "Stockholm (4)", "Göteborg (2)"]);
    const select = () => p.all("select")[0];
    expect(select().props.value).toBe("Malmö");
    select().props.onChange({ target: { value: "Göteborg" } });
    expect(select().props.value).toBe("Göteborg");
    const stock = dealerState().stock;
    stock.spark.Stockholm = 9;
    p.update(p.view({ ...o, stock }));
    expect(select().props.value).toBe("Göteborg");
    stock.spark.Göteborg = 0;
    p.update(p.view({ ...o, stock: { ...stock } }));
    expect(select().props.value).toBe("Stockholm");
    p.click("Deliver VO-1002");
    expect(p.onDeliver).toHaveBeenCalledExactlyOnceWith("VO-1002", "Stockholm");
  });

  it("does not move to another city by itself", () => {
    const o = { orders: [order("VO-1002", { model: "spark" })] };
    const p = page(o);
    const stock = dealerState().stock;
    stock.spark.Stockholm = 9;
    p.update(p.view({ ...o, stock }));
    expect(p.all("select")[0].props.value).toBe("Malmö");
  });

  it("is off while it is busy", () => {
    const p = page({ orders: [order("VO-1002", { model: "spark" })] }, ["VO-1002"]);
    expect(p.all("button").every((b) => b.props.disabled)).toBe(true);
    expect(p.all("select")[0].props.disabled).toBe(true);
  });

  it("shows markup in the address and the name as text", () => {
    const address = "<b>Storgatan</b> <script>alert(1)</script>";
    const p = page({ orders: [order("VO-1002", { address, name: "<i>Kim</i>" })] });
    expect(p.text()).toContain(address);
    expect(p.text()).toContain("<i>Kim</i>");
    for (const type of ["b", "i", "script"]) expect(p.all(type)).toEqual([]);
  });
});

describe("a delivered order", () => {
  const done = order("VO-1001", { status: "delivered", city: "Malmö", delivered: "2026-11-24T12:58:00Z", delivered_by: "Anna Rep" });
  it("says from where and by whom, and cannot be delivered", () => {
    const p = page({ orders: [done] });
    expect(p.text()).toContain("Delivered from Malmö");
    expect(p.text()).toContain("by Anna Rep");
    expect(p.text()).toContain("0 open");
    expect(p.has("Deliver VO-1001")).toBe(false);
    expect(p.has("None in stock")).toBe(false);
    expect(p.has("Delete VO-1001")).toBe(true);
  });
});

describe("Delete", () => {
  it("asks first, and does nothing when it is taken back", () => {
    const p = page({ orders: [order("VO-1002")] });
    expect(p.all("dialog")).toEqual([]);
    p.click("Delete VO-1002");
    const dialog = p.all("dialog")[0];
    expect(dialog.dom.open).toBe(true);
    expect(p.text()).toContain("Delete order VO-1002?");
    expect(p.onDelete).not.toHaveBeenCalled();
    p.click("Keep the order");
    expect(p.all("dialog")).toEqual([]);
    expect(dialog.dom.open).toBe(false); // closed, so that the browser gives the focus back
    expect(p.onDelete).not.toHaveBeenCalled();
  });

  it("is taken back with Escape", () => {
    const p = page({ orders: [order("VO-1002")] });
    p.click("Delete VO-1002");
    p.all("dialog")[0].props.onCancel({ preventDefault() {} });
    expect(p.all("dialog")).toEqual([]);
    expect(p.onDelete).not.toHaveBeenCalled();
  });

  it("deletes once when it is confirmed", () => {
    const p = page({ orders: [order("VO-1002")] });
    p.click("Delete VO-1002");
    p.click("Delete the order");
    expect(p.onDelete).toHaveBeenCalledExactlyOnceWith("VO-1002");
    expect(p.all("dialog")).toEqual([]);
  });

  it("closes the question when the order is gone meanwhile", () => {
    const p = page({ orders: [order("VO-1002")] });
    p.click("Delete VO-1002");
    p.update(p.view({ orders: [] }));
    expect(p.all("dialog")).toEqual([]);
  });
});

describe("no orders", () => {
  it("says so", () => {
    expect(page().text()).toContain("No orders yet.");
  });
});
