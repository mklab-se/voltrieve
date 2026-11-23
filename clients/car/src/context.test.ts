import { describe, expect, it } from "vitest";
import { carContext, START_ODOMETER_KM } from "./context";

const CLOCK = new Date(2026, 1, 3, 9, 5, 7);

describe("carContext", () => {
  it("tells which car it is, how fast it drives and what the clock shows", () => {
    expect(carContext({ distanceM: 2600, speedKmh: 47.4 }, CLOCK)).toMatchObject({
      car: { model: "Voltrieve Spark", trim: "Prime", drive: "RWD", model_year: 2026, registration: "SPARK", odometer_km: START_ODOMETER_KM + 2 },
      speed_kmh: 47,
    });
  });

  it("gives the local time with its offset, as the API wants it", () => {
    const { local_time } = carContext({ distanceM: 0, speedKmh: 0 }, CLOCK);
    expect(local_time).toMatch(/^2026-02-03T09:05:07[+-]\d{2}:\d{2}$/);
    expect(new Date(local_time!).getTime()).toBe(CLOCK.getTime());
  });

  it.each([
    [-60, "+01:00"],
    [0, "+00:00"],
    [210, "-03:30"],
    [-345, "+05:45"],
  ])("writes an offset of %i minutes as %s", (minutes, text) => {
    const clock = new Date(CLOCK);
    clock.getTimezoneOffset = () => minutes as number;
    expect(carContext({ distanceM: 0, speedKmh: 0 }, clock).local_time).toBe(`2026-02-03T09:05:07${text}`);
  });
});
