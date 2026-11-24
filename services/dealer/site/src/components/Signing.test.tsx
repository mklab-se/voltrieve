import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signaturePath } from "../signature";
import type { Order } from "../state";
import { dealerState, fakeApi, order, settle } from "../testing/sample";
import { show } from "../testing/react";

vi.mock("react", async () => (await import("../testing/react")).mocked);
const { Signing, flight, SIGNING_MS, STILL_MS } = await import("./Signing");
const { Desk } = await import("../App");

let reduced = false;
const stops: (() => void)[] = [];
beforeEach(() => {
  vi.useFakeTimers();
  reduced = false;
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduced-motion") && reduced }));
});
afterEach(() => {
  stops.splice(0).forEach((s) => s());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const A = order("VO-1001");
const B = order("VO-1002", { name: "Anna Lind", model: "spark", price_sek: 289000 });
const C = order("VO-1003");

function paper(o: Order, waiting = 0) {
  const onDone = vi.fn();
  const view = (next: Order) => <Signing key={next.number} order={next} model="Fjord" first waiting={waiting} onDone={onDone} />;
  const p = show(view(o));
  stops.push(p.unmount);
  return { ...p, view, onDone, text: () => p.text().replace(/ /g, " ") };
}

async function desk() {
  const f = fakeApi();
  const p = show(<Desk api={f.api} session={{ name: "Anna Rep", username: "anna@voltrieve.example" }} theme="light" onToggleTheme={() => {}} onSignOut={() => {}} />);
  stops.push(p.unmount);
  await settle();
  f.reads[0].resolve(dealerState());
  await settle();
  /** The state that the service has now, as the page learns it. */
  const now = async (...orders: Order[]) => {
    f.streams.at(-1)!.push({ event: "changed" });
    await settle();
    f.reads.at(-1)!.resolve(dealerState({ orders }));
    await settle();
  };
  const shown = () => p.all("dialog").map((d) => String(d.props["aria-label"]).match(/VO-\d+/)?.[0]);
  const dialog = () => p.all("dialog")[0];
  const click = (detail = 1) => dialog().props.onClick({ detail });
  const escape = (repeat = false) => {
    const e = { key: "Escape", repeat, preventDefault: vi.fn() };
    dialog().props.onKeyDown(e);
    return e;
  };
  return { ...f, ...p, press: p.click, now, shown, dialog, click, escape };
}

describe("the paper", () => {
  it("shows the order", () => {
    const p = paper(order("VO-1001", { created: new Date(2026, 10, 24, 14, 2).toISOString() }));
    for (const part of ["Voltrieve", "Purchase order", "VO-1001", "24 November 2026", "Fjord", "529 000 SEK", "Kim Berg", "Storgatan 1, Uppsala", "Demo order, not a real purchase"]) {
      expect(p.text()).toContain(part);
    }
  });

  it("has the signature of the name, and the number on the stamp", () => {
    const p = paper(A);
    expect(p.all("path").filter((e) => e.props.d === signaturePath("Kim Berg"))).not.toEqual([]);
    const [stamp] = p.all("div").filter((e) => e.props.className === "stamp");
    expect(p.all("strong", stamp.children).map((e) => p.name(e))).toEqual(["VO-1001"]);
  });

  it("shows markup in the name and the address as text", () => {
    const p = paper(order("VO-1001", { name: "<i>Kim</i>", address: "<b>Storgatan</b> 1" }));
    expect(p.text()).toContain("<i>Kim</i>");
    expect(p.text()).toContain("<b>Storgatan</b> 1");
    for (const type of ["i", "b"]) expect(p.all(type)).toEqual([]);
  });

  it("is a dialog with a label, opened once, with the focus on itself", () => {
    const p = paper(A);
    const [dialog] = p.all("dialog");
    expect(dialog.dom.open).toBe(true);
    expect(dialog.props["aria-label"]).toBe("Purchase order VO-1001, Fjord, for Kim Berg");
    expect(p.all("button")).toEqual([]);
    expect(p.all("div").filter((e) => e.props["aria-live"] || e.props.role === "status")).toEqual([]);
  });

  it("says how many wait", () => {
    expect(paper(A).text()).toContain("Click or press Escape to skip.");
    expect(paper(A, 1).text()).toContain("1 more order.");
    expect(paper(A, 2).text()).toContain("2 more orders.");
  });
});

describe("the signing", () => {
  it("takes five and a half seconds, also when the state is read meanwhile", () => {
    const p = paper(A);
    expect(SIGNING_MS).toBe(5500);
    vi.advanceTimersByTime(3000);
    p.update(p.view({ ...A, status: "delivered" }));
    vi.advanceTimersByTime(2499);
    expect(p.onDone).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(p.onDone).toHaveBeenCalledExactlyOnceWith("VO-1001");
  });

  it("with reduced motion is the finished paper for three seconds", () => {
    reduced = true;
    const p = paper(A);
    expect(STILL_MS).toBe(3000);
    expect(p.all("g").filter((e) => e.props.className === "pen")).toEqual([]);
    expect(p.all("dialog")[0].props.className).toContain("still");
    expect(p.all("path").filter((e) => e.props.d === signaturePath("Kim Berg"))).not.toEqual([]);
    vi.advanceTimersByTime(2999);
    expect(p.onDone).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(p.onDone).toHaveBeenCalledOnce();
  });

  it("does nothing more when it is taken away", () => {
    const p = paper(A);
    p.unmount();
    vi.advanceTimersByTime(20_000);
    expect(p.onDone).not.toHaveBeenCalled();
  });
});

describe("orders that arrive", () => {
  it("are not signed when they were there", async () => {
    const f = await desk();
    expect(f.shown()).toEqual([]);
  });

  it("are signed one after the other, the oldest first, each once", async () => {
    const f = await desk();
    await f.now(B, A);
    expect(f.shown()).toEqual(["VO-1001"]);
    await vi.advanceTimersByTimeAsync(5499);
    expect(f.shown()).toEqual(["VO-1001"]);
    await vi.advanceTimersByTimeAsync(1);
    expect(f.shown()).toEqual(["VO-1002"]);
    await f.now(C, B, A);
    expect(f.shown()).toEqual(["VO-1002"]);
    await vi.advanceTimersByTimeAsync(5500);
    expect(f.shown()).toEqual(["VO-1003"]);
    await vi.advanceTimersByTimeAsync(5500);
    expect(f.shown()).toEqual([]);
    await f.now(C, B, A);
    expect(f.shown()).toEqual([]);
  });

  it("are signed after a lost connection, and the ones before it are not again", async () => {
    const f = await desk();
    await f.now(A);
    f.click();
    f.streams[0].end();
    await vi.advanceTimersByTimeAsync(1000);
    f.streams[1].push({ event: "open" });
    await settle();
    f.reads.at(-1)!.resolve(dealerState({ orders: [B, A] }));
    await settle();
    expect(f.shown()).toEqual(["VO-1002"]);
  });

  it("are dismissed one at a time by a click, where a double click is one", async () => {
    const f = await desk();
    await f.now(B, A);
    f.click(1);
    f.click(2);
    expect(f.shown()).toEqual(["VO-1002"]);
    f.click(1);
    expect(f.shown()).toEqual([]);
  });

  it("are dismissed one at a time by Escape, where a held key is one", async () => {
    const f = await desk();
    await f.now(B, A);
    expect(f.escape().preventDefault).toHaveBeenCalled();
    f.escape(true);
    expect(f.shown()).toEqual(["VO-1002"]);
    f.escape();
    expect(f.shown()).toEqual([]);
  });

  it("are dismissed when the browser closes the dialog in its own way", async () => {
    const f = await desk();
    await f.now(B, A);
    f.dialog().props.onCancel({ preventDefault() {} });
    expect(f.shown()).toEqual(["VO-1002"]);
    f.dialog().props.onClose(); // a late event of the one before: this one is open
    expect(f.shown()).toEqual(["VO-1002"]);
    f.dialog().dom.close();
    f.dialog().props.onClose();
    expect(f.shown()).toEqual([]);
  });

  it("dim the page once for papers in a row, and again for a later one", async () => {
    const f = await desk();
    const first = () => String(f.dialog().props.className).split(" ").includes("first");
    await f.now(B, A);
    expect(first()).toBe(true);
    f.click();
    expect(first()).toBe(false);
    f.click();
    await f.now(C, B, A);
    expect(first()).toBe(true);
  });

  it("leave no timer of a dismissed one behind", async () => {
    const f = await desk();
    await f.now(B, A);
    await vi.advanceTimersByTimeAsync(3000);
    f.click();
    await vi.advanceTimersByTimeAsync(5499);
    expect(f.shown()).toEqual(["VO-1002"]);
    await vi.advanceTimersByTimeAsync(1);
    expect(f.shown()).toEqual([]);
  });

  it("leave the page usable, with the focus where it was", async () => {
    const f = await desk();
    const plus = f.button("One Spark more in Malmö").dom;
    plus.focus();
    await f.now(B, A);
    expect(f.focused.on).toBe(f.dialog().dom);
    const first = f.dialog().dom;
    f.click();
    f.click();
    expect(first.open).toBe(false);
    expect(f.focused.on).toBe(plus);
    f.press("One Spark more in Malmö");
    expect(f.writes.map((w) => w.call)).toEqual([["changeStock", "spark", "Malmö", 1]]);
  });

  it("come over an open question, and leave it open", async () => {
    const f = await desk();
    await f.now(A);
    f.click();
    f.press("Delete VO-1001");
    const keep = f.button("Keep the order").dom;
    keep.focus();
    await f.now(B, A);
    expect(f.all("dialog").map((d) => d.dom.open)).toEqual([true, true]);
    expect(f.writes).toEqual([]);
    f.all("dialog").find((d) => String(d.props["aria-label"]).includes("VO-1002"))!.props.onClick({ detail: 1 });
    expect(f.all("dialog")).toHaveLength(1);
    expect(f.text()).toContain("Delete order VO-1001?");
    expect(f.focused.on).toBe(keep);
  });

  it("are gone when the order is deleted meanwhile, or the account loses its access", async () => {
    const f = await desk();
    await f.now(B, A);
    await f.now(B);
    expect(f.shown()).toEqual(["VO-1002"]);
    f.streams[0].push({ event: "changed" });
    await settle();
    f.reads.at(-1)!.reject(f.error(403, "no"));
    await settle();
    expect(f.shown()).toEqual([]);
  });

  it("mark their row when they have landed", async () => {
    const f = await desk();
    await f.now(A);
    const row = () => String(f.all("li").find((li) => li.props["data-order"] === "VO-1001")!.props.className);
    expect(row()).not.toContain("landed");
    await vi.advanceTimersByTimeAsync(5500);
    expect(row()).toContain("landed");
  });
});

describe("a hidden tab", () => {
  /** A document of which only the visibility matters. */
  function tab() {
    const doc = Object.assign(new EventTarget(), { visibilityState: "visible", querySelectorAll: () => [] });
    vi.stubGlobal("document", doc);
    return (state: "visible" | "hidden") => {
      doc.visibilityState = state;
      doc.dispatchEvent(new Event("visibilitychange"));
    };
  }

  it.each([["with motion", false, 5500], ["with reduced motion", true, 3000]] as const)("makes the papers wait, and starts them when it is shown, %s", async (_, still, ms) => {
    reduced = still;
    const turn = tab();
    const f = await desk();
    turn("hidden");
    await f.now(B, A);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(f.shown()).toEqual([]);
    turn("visible");
    expect(f.shown()).toEqual(["VO-1001"]);
    expect(String(f.dialog().props.className).split(" ")).toContain("first");
    await vi.advanceTimersByTimeAsync(ms - 1);
    expect(f.shown()).toEqual(["VO-1001"]);
    await vi.advanceTimersByTimeAsync(1);
    expect(f.shown()).toEqual(["VO-1002"]);
    await vi.advanceTimersByTimeAsync(ms);
    expect(f.shown()).toEqual([]);
  });

  it("starts a paper over that was playing", async () => {
    const turn = tab();
    const f = await desk();
    const plus = f.button("One Spark more in Malmö").dom;
    plus.focus();
    await f.now(A);
    await vi.advanceTimersByTimeAsync(5000); // it is flying
    const old = f.dialog();
    turn("hidden");
    expect(f.shown()).toEqual([]);
    expect(old.dom.open).toBe(false);
    await vi.advanceTimersByTimeAsync(20_000);
    turn("visible");
    expect(f.shown()).toEqual(["VO-1001"]);
    expect(String(f.dialog().props.className)).not.toContain("gone");
    await vi.advanceTimersByTimeAsync(5499);
    expect(f.shown()).toEqual(["VO-1001"]);
    await vi.advanceTimersByTimeAsync(1);
    expect(f.shown()).toEqual([]);
    expect(f.focused.on).toBe(plus);
  });

  it("is not a skip when the dialog that went away says that it closed", async () => {
    const turn = tab();
    const f = await desk();
    await f.now(A);
    const old = f.dialog();
    turn("hidden");
    old.props.ref.current = null; // as React does
    old.props.onClose();
    turn("visible");
    expect(f.shown()).toEqual(["VO-1001"]);
  });

  it("does not bring back what was skipped or deleted", async () => {
    const turn = tab();
    const f = await desk();
    await f.now(B, A);
    f.click();
    turn("hidden");
    await f.now(A);
    turn("visible");
    expect(f.shown()).toEqual([]);
  });

  it("is started in as well", async () => {
    const turn = tab();
    turn("hidden");
    const f = await desk();
    await f.now(A);
    expect(f.shown()).toEqual([]);
    turn("visible");
    expect(f.shown()).toEqual(["VO-1001"]);
  });
});

describe("the flight", () => {
  const rect = (x: number, y: number, width: number, height: number) => ({ left: x, top: y, width, height });
  it("goes from the middle of the paper to the middle of the row, and shrinks to its height", () => {
    expect(flight(rect(400, 100, 600, 700), rect(20, 120, 900, 140))).toEqual({ x: -230, y: -260, scale: 0.2 });
  });
  it("shrinks where it is without a row", () => {
    expect(flight(rect(400, 100, 600, 700), undefined)).toEqual({ x: 0, y: 0, scale: 0.2 });
    expect(flight(rect(0, 0, 0, 0), rect(0, 0, 10, 10)).scale).toBe(0.2);
  });
});
