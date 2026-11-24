import { describe, expect, it, vi } from "vitest";
import { askToPark, drivesWith, PARK_QUESTION, runShown, typesInto } from "./ask";

describe("Park here?", () => {
  it("sends the question with the photo", async () => {
    const send = vi.fn();
    await askToPark(async () => ({ id: "att1" }), send);
    expect(send).toHaveBeenCalledWith(PARK_QUESTION, ["att1"]);
    expect(PARK_QUESTION).toBe("Can I park here?");
  });

  it("sends the question alone when the photo is not good, so that Runa says why", async () => {
    const send = vi.fn();
    await askToPark(async () => ({}), send);
    expect(send).toHaveBeenCalledWith(PARK_QUESTION, undefined);
  });

  it("sends the question alone when the upload breaks", async () => {
    const send = vi.fn();
    await askToPark(() => Promise.reject(new Error("no token")), send);
    expect(send).toHaveBeenCalledWith(PARK_QUESTION, undefined);
  });
});

describe("a new chat in the middle", () => {
  it("sends no question about the photo of the old one", async () => {
    const send = vi.fn();
    let now = true;
    const asking = askToPark(async () => ({ id: "att1" }), send, () => now);
    now = false;
    await asking;
    expect(send).not.toHaveBeenCalled();
  });

  it("is not told what a tool of the old one does or has done", async () => {
    const doing = vi.fn();
    const asked = vi.fn();
    let now = true;
    let finish = (_: { output: string; attachments?: string[] }) => {};
    const call = { call_id: "1", name: "get_sign_photo", arguments: "{}" };
    const running = runShown(call, () => new Promise((resolve) => (finish = resolve)), { current: () => now, doing, asked });
    expect(doing.mock.calls).toEqual([["Looking at the sign"]]);
    now = false;
    finish({ output: "x", attachments: ["att1"] });
    expect(await running).toEqual({ output: "x", attachments: ["att1"] });
    expect(doing).toHaveBeenCalledTimes(1);
    expect(asked).not.toHaveBeenCalled();
  });

  it("is told otherwise, also when the tool fails", async () => {
    const doing = vi.fn();
    const asked = vi.fn();
    const call = { call_id: "1", name: "park", arguments: "{}" };
    const io = { current: () => true, doing, asked };
    await runShown(call, async () => ({ output: "x", attachments: ["att1"] }), io);
    expect(asked).toHaveBeenCalledTimes(1);
    await expect(runShown(call, () => Promise.reject(new Error("no")), io)).rejects.toThrow("no");
    expect(doing.mock.calls).toEqual([["Parking the car"], [undefined], ["Parking the car"], [undefined]]);
  });
});

describe("the up and down keys", () => {
  const element = (tagName: string, scrolls?: { height: number; seen: number }) => ({
    tagName,
    closest: (selector: string) =>
      selector === "[data-scroll]" && scrolls ? { scrollHeight: scrolls.height, clientHeight: scrolls.seen } : null,
  });

  it("drive, also from a button", () => {
    expect(drivesWith({ key: "ArrowUp" }, element("BODY"))).toBe("accelerate");
    expect(drivesWith({ key: "ArrowDown" }, element("BUTTON"))).toBe("brake");
    expect(drivesWith({ key: "ArrowDown" }, null)).toBe("brake");
    expect(drivesWith({ key: "ArrowLeft" }, null)).toBeUndefined();
  });

  it("do not drive in a field, with another key, or where they scroll", () => {
    expect(drivesWith({ key: "ArrowUp" }, element("INPUT"))).toBeUndefined();
    expect(drivesWith({ key: "ArrowUp", metaKey: true }, null)).toBeUndefined();
    expect(drivesWith({ key: "ArrowUp" }, element("DIV", { height: 900, seen: 300 }))).toBeUndefined();
  });

  it("drive where there is nothing to scroll", () => {
    expect(drivesWith({ key: "ArrowUp" }, element("DIV", { height: 300, seen: 300 }))).toBe("accelerate");
  });
});

describe("typing anywhere", () => {
  const key = (k: string, more = {}) => ({ key: k, ctrlKey: false, metaKey: false, altKey: false, isComposing: false, ...more });

  it.each(["a", "Ö", "7", "?"])("%s goes to the chat field", (k) => expect(typesInto(key(k), "BODY")).toBe(true));

  it.each(["ArrowUp", "ArrowDown", "Escape", "Enter", "Tab", "F5", "Shift"])("%s does not", (k) =>
    expect(typesInto(key(k), "BODY")).toBe(false),
  );

  it("leaves shortcuts alone", () => {
    expect(typesInto(key("c", { metaKey: true }), "BODY")).toBe(false);
    expect(typesInto(key("r", { ctrlKey: true }), "BODY")).toBe(false);
  });

  it("leaves other fields alone, and the space on a button", () => {
    for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) expect(typesInto(key("a"), tag)).toBe(false);
    expect(typesInto(key(" "), "BUTTON")).toBe(false);
    expect(typesInto(key("a"), "BUTTON")).toBe(true);
    expect(typesInto(key(" "), "BODY")).toBe(false);
  });
});
