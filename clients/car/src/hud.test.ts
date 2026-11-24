import { describe, expect, it } from "vitest";
import { CLOSE_AFTER_MS, hudNotice, hudReducer, initialHud, type HudEvent, type HudState } from "./hud";

/** Run these events, each with its time. */
const run = (events: [HudEvent, number][], from: HudState = initialHud) =>
  events.reduce((state, [event, now]) => hudReducer(state, event, now), from);

describe("the HUD", () => {
  it("is closed until the driver says something", () => {
    expect(initialHud.open).toBe(false);
    expect(run([["tick", 5], ["delta", 6], ["enter", 7]]).open).toBe(false);
  });

  it("opens on a message", () => {
    expect(run([["send", 0]]).open).toBe(true);
  });

  it("stays open while Runa works, however long it takes", () => {
    expect(run([["send", 0], ["tick", 60_000]]).open).toBe(true);
    expect(run([["send", 0], ["delta", 100], ["tick", 60_000]]).open).toBe(true);
  });

  it("goes back 12 seconds after the last of the answer, and not a moment before", () => {
    const answered = run([["send", 0], ["delta", 500], ["delta", 1000], ["done", 1000]]);
    expect(CLOSE_AFTER_MS).toBe(12_000);
    expect(hudReducer(answered, "tick", 12_999).open).toBe(true);
    expect(hudReducer(answered, "tick", 13_000).open).toBe(false);
  });

  it("goes back after an answer without any text", () => {
    expect(run([["send", 0], ["done", 1000], ["tick", 13_000]]).open).toBe(false);
  });

  it("stays while the pointer or the focus is in it", () => {
    const held = run([["send", 0], ["delta", 1000], ["done", 1000], ["enter", 2000]]);
    expect(hudReducer(held, "tick", 99_000).open).toBe(true);
    const left = hudReducer(held, "leave", 99_000);
    expect(hudReducer(left, "tick", 99_500).open).toBe(true); // the time starts again
    expect(hudReducer(left, "tick", 111_000).open).toBe(false);
  });

  it("goes back on Escape, also while Runa works or it is held", () => {
    expect(run([["send", 0], ["escape", 10]]).open).toBe(false);
    expect(run([["send", 0], ["enter", 5], ["escape", 10]]).open).toBe(false);
  });

  it("does not open again by itself for the rest of the answer", () => {
    expect(run([["send", 0], ["escape", 10], ["delta", 20], ["done", 30]]).open).toBe(false);
  });

  it("opens again on the next message, with the time from the start", () => {
    const again = run([["send", 0], ["delta", 10], ["done", 20], ["tick", 13_000], ["send", 14_000]]);
    expect(again.open).toBe(true);
    expect(hudReducer(again, "tick", 14_001).open).toBe(true);
    expect(again.hold).toBe(false);
  });
});

describe("the notice about the block", () => {
  const asked = (askedBlock: number | undefined, currentBlock: number, open = true): HudState => ({
    ...initialHud,
    open,
    askedBlock,
    currentBlock,
  });

  it("is shown when the car has come to another block", () => {
    expect(hudNotice(asked(3, 4))).toBe("This answer is for the previous block.");
  });

  it("is not shown in the same block, when nothing was asked about a sign, or when the HUD is closed", () => {
    expect(hudNotice(asked(3, 3))).toBeUndefined();
    expect(hudNotice(asked(undefined, 4))).toBeUndefined();
    expect(hudNotice(asked(3, 4, false))).toBeUndefined();
  });

  it("is gone with the next message", () => {
    expect(hudReducer(asked(3, 4), "send", 0).askedBlock).toBeUndefined();
  });
});
