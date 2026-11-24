import { describe, expect, it, vi } from "vitest";
import { createMotor, motorTone } from "./motor";

describe("the sound of the motor", () => {
  it("is higher and louder the faster the car drives, and silent at a standstill", () => {
    expect(motorTone(0).volume).toBe(0);
    expect(motorTone(90).hz).toBeGreaterThan(motorTone(30).hz);
    expect(motorTone(90).volume).toBeGreaterThan(motorTone(10).volume);
    expect(motorTone(150).volume).toBeLessThan(0.1);
  });

  it("makes no sound and opens no audio until it is turned on", () => {
    const makeAudio = vi.fn();
    const motor = createMotor(makeAudio);
    motor.setSpeed(50);
    motor.setOn(false);
    expect(makeAudio).not.toHaveBeenCalled();
  });

  it("stays off when the browser has no audio", () => {
    const motor = createMotor(() => {
      throw new Error("no audio");
    });
    expect(() => motor.setOn(true)).not.toThrow();
    expect(() => motor.setSpeed(50)).not.toThrow();
  });

  it("is silent while the tab is hidden, and comes back", () => {
    const volumes: number[] = [];
    const param = (note?: (v: number) => void) => ({ value: 0, setTargetAtTime: (v: number) => note?.(v) });
    const node = (gain?: (v: number) => void) => {
      const n = { gain: param(gain), frequency: param(), type: "", start() {}, connect: (to: unknown) => to };
      return n;
    };
    let gains = 0;
    const audio = {
      currentTime: 0,
      destination: {},
      resume: async () => {},
      createGain: () => node((gains += 1) === 1 ? (v) => volumes.push(v) : undefined),
      createOscillator: () => node(),
      createBiquadFilter: () => node(),
    };
    const motor = createMotor(() => audio as unknown as AudioContext);
    motor.setSpeed(50);
    motor.setOn(true);
    expect(volumes.at(-1)).toBe(motorTone(50).volume);
    motor.setHidden(true);
    expect(volumes.at(-1)).toBe(0);
    motor.setSpeed(60);
    expect(volumes.at(-1)).toBe(0);
    motor.setHidden(false);
    expect(volumes.at(-1)).toBe(motorTone(60).volume);
  });
});
