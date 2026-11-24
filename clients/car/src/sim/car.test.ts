import { describe, expect, it } from "vitest";
import { newCar, setSpeed, startParking, step, type Car } from "./car";
import { BAYS_AT_M, BLOCK_M, blockOf } from "./street";

const DT = 1 / 60;

function run(start: Car, seconds: number, dt = DT): Car {
  let c = start;
  for (let i = 0; i < Math.round(seconds / dt); i += 1) c = step(c, dt);
  return c;
}

/** The seconds it takes to get from one speed to another. */
function seconds(from: number, to: number): number {
  let c = setSpeed(car(from), to);
  let steps = 0;
  for (; c.speedKmh !== to && steps < 10_000; steps += 1) c = step(c, DT);
  return steps * DT;
}

const car = (speedKmh: number, distanceM = 0): Car => ({ ...newCar(), distanceM, speedKmh, targetKmh: speedKmh });
const parked = () => run(startParking(newCar()), 60);
const isBay = (m: number) => (BAYS_AT_M as readonly number[]).includes(m % BLOCK_M);

describe("speed", () => {
  it("accelerates like the Spark", () => {
    expect(seconds(0, 100)).toBeCloseTo(9.2, 1);
    expect(run(setSpeed(newCar(), 200), 60).speedKmh).toBe(150);
  });

  it("brakes to a stop and stays there", () => {
    expect(seconds(90, 0)).toBeCloseTo(90 / 25, 1);
    const stopped = run(setSpeed(car(90), 0), 10);
    expect(stopped.speedKmh).toBe(0);
    expect(step(stopped, DT)).toEqual(stopped);
  });

  it("never goes backwards and takes no target that is not a number", () => {
    expect(setSpeed(car(50), -10).targetKmh).toBe(0);
    expect(setSpeed(car(50), Number.NaN).targetKmh).toBe(50);
  });

  it("drives as far as the speed says", () => {
    expect(run(car(36), 10).distanceM).toBeCloseTo(100, 6);
  });

  it("does not jump after a pause: a step is at most 0.1 s", () => {
    expect(step(car(50), 60).distanceM - car(50).distanceM).toBeLessThan(2);
    expect(step(car(50), 60)).toEqual(step(car(50), 0.1));
    expect(step(car(50), -1)).toEqual(car(50));
    expect(step(car(50), Number.NaN)).toEqual(car(50));
  });

  it("leaves the car it is given alone", () => {
    const before = Object.freeze(car(50));
    expect(step(before, DT)).not.toBe(before);
  });
});

describe("parking", () => {
  it("creeps from a standstill", () => {
    expect(run(startParking(car(0)), 60).mode).toBe("parked");
    const creeping = run(startParking(car(0)), 5);
    expect(creeping).toMatchObject({ mode: "parking", speedKmh: 15, bayM: 60 });
  });

  it("brakes into a bay from speed", () => {
    expect(run(startParking(car(90)), 60)).toMatchObject({ mode: "parked", speedKmh: 0, lateral: 1 });
  });

  it.each([0, 5, 30, 90, 150])("stops exactly in the bay from %i km/h, wherever it is", (kmh) => {
    for (const dt of [1 / 144, 1 / 60, 0.1]) {
      for (let at = 0; at < BLOCK_M; at += 13) {
        const start = startParking(car(kmh, at));
        const end = run(start, 60, dt);
        expect(end).toMatchObject({ mode: "parked", speedKmh: 0, targetKmh: 0, lateral: 1, distanceM: start.bayM });
        expect(isBay(end.distanceM)).toBe(true);
      }
    }
  });

  it("brakes no harder than the car can, and moves sideways only near the bay", () => {
    let c = startParking(car(150, 100));
    while (c.mode !== "parked") {
      const next = step(c, DT);
      if (next.mode !== "parked") expect(c.speedKmh - next.speedKmh).toBeLessThanOrEqual(25 * DT + 1e-6);
      expect(next.distanceM).toBeGreaterThanOrEqual(c.distanceM);
      if (c.bayM! - c.distanceM > 12.5) expect(c.lateral).toBe(0);
      expect(next.lateral).toBeGreaterThanOrEqual(c.lateral);
      c = next;
    }
  });

  it("keeps its bay over a pause, so it does not park in the wrong block", () => {
    const start = startParking(car(50, 300));
    const end = run(step(run(start, 1), 3600), 60);
    expect(end.distanceM).toBe(start.bayM);
    expect(blockOf(end.distanceM)).toBe(blockOf(start.bayM!));
  });

  it("stays where it is when it is parked or on its way", () => {
    const there = parked();
    expect(startParking(there)).toEqual(there);
    expect(run(there, 5)).toEqual(there);
    const onTheWay = run(startParking(car(50)), 1);
    expect(startParking(onTheWay)).toEqual(onTheWay);
  });

  it("pulls out of the bay first", () => {
    expect(run(setSpeed(parked(), 30), 10)).toMatchObject({ mode: "driving", lateral: 0 });
    expect(run(setSpeed(parked(), 30), 1).mode).toBe("leaving");
    expect(run(setSpeed(parked(), 30), 10).bayM).toBeUndefined();
    expect(setSpeed(parked(), 0).mode).toBe("parked");
  });

  it("gives the parking up when a speed is set", () => {
    expect(setSpeed(run(startParking(car(50)), 1), 80)).toMatchObject({ mode: "driving", targetKmh: 80 });
    const halfIn = run(startParking(car(0, 45)), 4.5);
    expect(halfIn.lateral).toBeGreaterThan(0);
    const out = setSpeed(halfIn, 30);
    expect(out.mode).toBe("leaving");
    expect(run(out, 10)).toMatchObject({ mode: "driving", lateral: 0, speedKmh: 30 });
  });

  it("finishes pulling out when it is told to stop half way, and stops in the lane", () => {
    const halfOut = run(setSpeed(parked(), 30), 1.5);
    expect(halfOut.lateral).toBeGreaterThan(0);
    const stopped = run(setSpeed(halfOut, 0), 20);
    expect(stopped).toMatchObject({ mode: "driving", lateral: 0, speedKmh: 0 });
    expect(stopped.distanceM - halfOut.distanceM).toBeLessThan(15);
  });

  it("parks again while it is still leaving", () => {
    const leaving = run(setSpeed(parked(), 30), 1.5);
    expect(leaving.lateral).toBeGreaterThan(0);
    expect(run(startParking(leaving), 60)).toMatchObject({ mode: "parked", lateral: 1, distanceM: 130 });
  });
});
