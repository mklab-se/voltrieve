import { describe, expect, it } from "vitest";
import { PARKED, stepWipers, wiperPosition, type Wipers } from "./wipers";
import type { WiperMode } from "../tools";

/** How often they leave the bottom in this many seconds. */
function sweeps(mode: WiperMode, seconds: number): number {
  let w: Wipers = PARKED;
  let n = 0;
  for (let t = 0; t < seconds; t += 1 / 60) {
    const before = w.phase;
    w = stepWipers(w, mode, 1 / 60);
    if (before > w.phase) n += 1;
  }
  return n;
}

describe("the wipers", () => {
  it("rest at the bottom", () => {
    expect(wiperPosition(PARKED.phase)).toBe(0);
    expect(stepWipers(PARKED, "off", 1)).toEqual(PARKED);
  });

  it("go up and down again", () => {
    expect(wiperPosition(0.25)).toBeCloseTo(0.5);
    expect(wiperPosition(0.5)).toBe(1);
    expect(wiperPosition(0.75)).toBeCloseTo(0.5);
    expect(wiperPosition(1.4)).toBe(0); // the pause
  });

  it("sweep more often when they are fast", () => {
    expect(sweeps("slow", 30)).toBeGreaterThan(8);
    expect(sweeps("fast", 30)).toBeGreaterThan(2 * sweeps("slow", 30));
  });

  it("finish the sweep when they are turned off", () => {
    let w: Wipers = { ...PARKED, phase: 0.5 };
    w = stepWipers(w, "off", 0.1);
    expect(wiperPosition(w.phase)).toBeGreaterThan(0);
    for (let i = 0; i < 60; i += 1) w = stepWipers(w, "off", 1 / 60);
    expect(w.phase).toBe(0);
  });

  it("do not jump when the speed changes", () => {
    const fast = stepWipers({ ...PARKED, phase: 0.4 }, "fast", 0.01);
    expect(Math.abs(wiperPosition(fast.phase) - wiperPosition(0.4))).toBeLessThan(0.1);
  });
});
