import { describe, expect, it, vi } from "vitest";
import { askToPark, PARK_QUESTION, typesInto } from "./ask";

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
