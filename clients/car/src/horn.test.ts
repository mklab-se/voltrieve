import { describe, expect, it, vi } from "vitest";
import { createHorn } from "./horn";

function fakeAudio(state: "running" | "suspended", allowed = false) {
  const tones: { start: number; stop: number }[] = [];
  const param = () => ({ value: 0, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() });
  const audio = {
    state: state as string,
    currentTime: 1,
    destination: {},
    resume: vi.fn(() => {
      if (!allowed) return new Promise<void>(() => {}); // what a browser does before the first click
      audio.state = "running";
      return Promise.resolve();
    }),
    createGain: () => ({ gain: param(), connect: vi.fn() }),
    createOscillator: () => {
      const tone = { start: 0, stop: 0 };
      tones.push(tone);
      return {
        type: "",
        frequency: param(),
        connect: vi.fn(),
        start: (at: number) => (tone.start = at),
        stop: (at: number) => (tone.stop = at),
      };
    },
  };
  return { audio: audio as unknown as AudioContext, tones, resume: audio.resume };
}

describe("the horn", () => {
  it("honks as many times as asked, one after the other", async () => {
    const { audio, tones } = fakeAudio("running");
    expect(await createHorn(() => audio, 0)(3)).toBe(true);
    const starts = [...new Set(tones.map((t) => t.start))];
    expect(starts).toHaveLength(3);
    expect(starts[0]).toBeGreaterThanOrEqual(1);
    expect(Math.max(...tones.map((t) => t.stop))).toBeGreaterThan(starts[2]!);
    expect(starts[1]).toBeGreaterThan(tones[0]!.stop);
  });

  it("says no when the browser blocks the sound, and plays nothing later", async () => {
    const { audio, tones } = fakeAudio("suspended");
    expect(await createHorn(() => audio, 5)(2)).toBe(false);
    expect(tones).toEqual([]);
  });

  it("wakes the sound after a click", async () => {
    const { audio, tones, resume } = fakeAudio("suspended", true);
    expect(await createHorn(() => audio, 5)(1)).toBe(true);
    expect(resume).toHaveBeenCalled();
    expect(tones.length).toBeGreaterThan(0);
  });

  it("uses one audio context, and says no when there is none", async () => {
    const make = vi.fn(() => fakeAudio("running").audio);
    const horn = createHorn(make, 0);
    await horn(1);
    await horn(1);
    expect(make).toHaveBeenCalledTimes(1);
    const none = createHorn(() => {
      throw new Error("no audio");
    }, 0);
    expect(await none(1)).toBe(false);
  });
});
