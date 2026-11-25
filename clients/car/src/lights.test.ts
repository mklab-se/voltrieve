import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createLights, FLASH_MS } from "./lights";
// @ts-expect-error: the car has no types of Node, and only this test reads a file.
import { readFileSync } from "node:fs";

beforeEach(() => void vi.useFakeTimers());
afterEach(() => void vi.useRealTimers());

describe("the lights", () => {
  it("flash 0.6 s for each time", () => {
    const on = vi.fn();
    createLights(on).flash(3);
    expect(on).toHaveBeenLastCalledWith(true);
    vi.advanceTimersByTime(3 * FLASH_MS - 1);
    expect(on).toHaveBeenLastCalledWith(true);
    vi.advanceTimersByTime(1);
    expect(on).toHaveBeenLastCalledWith(false);
  });

  it("flash more after those going on, and not over them", () => {
    const on = vi.fn();
    const lights = createLights(on);
    lights.flash(2);
    vi.advanceTimersByTime(500);
    lights.flash(1);
    vi.advanceTimersByTime(3 * FLASH_MS - 501);
    expect(on).not.toHaveBeenCalledWith(false);
    vi.advanceTimersByTime(1);
    expect(on).toHaveBeenLastCalledWith(false);
  });

  it("stop at once", () => {
    const on = vi.fn();
    const lights = createLights(on);
    lights.flash(5);
    lights.stop();
    expect(on).toHaveBeenLastCalledWith(false);
    vi.advanceTimersByTime(10_000);
    expect(on).toHaveBeenCalledTimes(2);
  });

  it("shine all the time for who wants less motion: what flashes is an animation, and those are off", () => {
    const css: string = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
    expect(css).toContain(`.flashing { animation: flash ${FLASH_MS / 1000}s infinite; }`);
    expect(css).toMatch(/prefers-reduced-motion: reduce\) \{\s+\*, \*::before, \*::after \{ animation: none !important; \}/);
  });
});
