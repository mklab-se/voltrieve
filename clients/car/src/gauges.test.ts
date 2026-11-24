import { describe, expect, it } from "vitest";
import { lastSignBlock, readGauges } from "./gauges";
import { newCar, type Car } from "./sim/car";

const car = (change: Partial<Car> = {}): Car => ({ ...newCar(), ...change });

describe("the block of the last sign", () => {
  it.each([
    [0, undefined],
    [9.9, undefined],
    [10, 0],
    [405, 0],
    [410, 1],
    [4009, 9],
  ])("at %d m is %s", (m, block) => expect(lastSignBlock(m)).toBe(block));
});

describe("the gauges", () => {
  it("show the speed in whole numbers", () => {
    expect(readGauges(car({ speedKmh: 49.6, targetKmh: 50 }), "off").speed).toBe(50);
  });

  it("show P when the car stands, and D when it drives", () => {
    expect(readGauges(car(), "off").gear).toBe("P");
    expect(readGauges(car({ mode: "parked", lateral: 1 }), "off").gear).toBe("P");
    expect(readGauges(car({ targetKmh: 50 }), "off").gear).toBe("D");
    expect(readGauges(car({ speedKmh: 20, targetKmh: 0 }), "off").gear).toBe("D");
    expect(readGauges(car({ mode: "parking", speedKmh: 15, targetKmh: 15 }), "off").gear).toBe("D");
  });

  it("show a range that falls with the distance, and never below zero", () => {
    const start = readGauges(car(), "off");
    expect(start.rangeKm).toBe(248);
    expect(start.batteryPercent).toBe(80);
    expect(readGauges(car({ distanceM: 10_000 }), "off").rangeKm).toBe(238);
    expect(readGauges(car({ distanceM: 31_000 }), "off").batteryPercent).toBe(70);
    expect(readGauges(car({ distanceM: 9_000_000 }), "off")).toMatchObject({ rangeKm: 0, batteryPercent: 0 });
  });

  it("show how far it is to the next sign and from the last one, in steps of ten meters", () => {
    expect(readGauges(car({ distanceM: 293 }), "off")).toMatchObject({ signInM: 120, signAgoM: 280, block: 0 });
    expect(readGauges(car({ distanceM: 2 }), "off")).toMatchObject({ signInM: 10, signAgoM: undefined, block: undefined });
    expect(readGauges(car({ distanceM: 410 }), "off")).toMatchObject({ signInM: 400, signAgoM: 0, block: 1 });
  });

  it("show the wipers", () => {
    expect(readGauges(car(), "fast").wipers).toBe("fast");
  });
});
