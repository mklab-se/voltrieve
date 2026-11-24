import { describe, expect, it } from "vitest";
import { Quality } from "./quality";

/** Frames of this length, for this long. */
function play(q: Quality, fps: number, seconds: number) {
  for (let i = 0; i < fps * seconds; i += 1) q.frame(1 / fps);
}

describe("the quality of the picture", () => {
  it("stays at its best at 60 and at a steady 30 frames a second", () => {
    for (const fps of [120, 60, 30]) {
      const q = new Quality();
      play(q, fps, 30);
      expect(q.level).toBe(0);
    }
  });

  it("falls while the frames are slow, and not below the last step", () => {
    const q = new Quality();
    play(q, 18, 6);
    expect(q.level).toBe(1);
    play(q, 18, 120);
    expect(q.level).toBe(3);
  });

  it("does not fall for one hitch, or for a tab that was hidden", () => {
    const q = new Quality();
    for (let i = 0; i < 600; i += 1) q.frame(i % 10 === 0 ? 0.2 : 1 / 60);
    q.frame(60);
    expect(q.level).toBe(0);
  });

  it("comes back when the frames are good again", () => {
    const q = new Quality();
    play(q, 18, 6);
    play(q, 60, 60);
    expect(q.level).toBe(0);
  });

  it("waits longer each time that coming back was too much", () => {
    const q = new Quality();
    let changes = 0;
    let before = 0;
    // A computer that manages level 1 and not level 0.
    for (let t = 0; t < 600; ) {
      const dt = q.level === 0 ? 1 / 18 : 1 / 60;
      q.frame(dt);
      t += dt;
      if (q.level !== before) changes += 1;
      before = q.level;
    }
    expect(changes).toBeLessThan(14);
  });
});
