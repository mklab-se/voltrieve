import { describe, expect, it } from "vitest";
import { CENTER_SCREEN, DRIVER_DISPLAY, fitPicture, HUD_BOTTOM, hudHeight, MIRROR, WINDSCREEN } from "./layout";

describe("the cockpit picture", () => {
  it.each([
    [1280, 720],
    [1440, 900],
    [1920, 1080],
    [2560, 1440],
    [3440, 1440],
    [920, 720],
    [1280, 1024],
  ])("covers a window of %i by %i without bars, and keeps its shape", (width, height) => {
    const box = fitPicture(width, height);
    expect(box.left).toBeLessThanOrEqual(0);
    expect(box.top).toBeLessThanOrEqual(0);
    expect(box.left + box.width).toBeGreaterThanOrEqual(width - 0.01);
    expect(box.top + box.height).toBeGreaterThanOrEqual(height - 0.01);
    expect(box.width / box.height).toBeCloseTo(1.5);
  });

  it("loses only its bottom at 16:9", () => {
    expect(fitPicture(1920, 1080)).toEqual({ left: 0, top: 0, width: 1920, height: 1280 });
  });

  it("is centered when the window is narrow", () => {
    const box = fitPicture(1280, 1024);
    expect(box.left).toBe(-128);
    expect(box.top).toBe(0);
  });

  it.each([
    [2560, 1080],
    [3440, 1440],
    [920, 720],
  ])("shows both displays whole at %i by %i", (width, height) => {
    const box = fitPicture(width, height);
    for (const display of [CENTER_SCREEN, DRIVER_DISPLAY]) {
      expect(box.top + ((display.top + display.height) / 100) * box.height).toBeLessThanOrEqual(height);
      expect(box.left + (display.left / 100) * box.width).toBeGreaterThanOrEqual(0);
    }
  });

  it("has the mirror inside the part where the street is drawn", () => {
    expect(MIRROR.left).toBeGreaterThan(WINDSCREEN.left);
    expect(MIRROR.top).toBeGreaterThan(WINDSCREEN.top);
    expect(MIRROR.top + MIRROR.height).toBeLessThan(WINDSCREEN.top + WINDSCREEN.height);
  });

  it.each([
    [1440, 900],
    [2560, 1080],
    [3440, 1440],
  ])("has room for the whole HUD at %i by %i", (width, height) => {
    const box = fitPicture(width, height);
    const top = box.top + (HUD_BOTTOM / 100) * box.height - hudHeight(box);
    expect(top).toBeGreaterThanOrEqual(8);
    expect(hudHeight(box)).toBeGreaterThan(height * 0.25);
  });
});
