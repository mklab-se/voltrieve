import type { ClientContext } from "./types";

/** What the made-up Spark had driven before the trip. */
export const START_ODOMETER_KM = 8214;

const CAR = { model: "Voltrieve Spark", trim: "Prime", drive: "RWD", model_year: 2026, registration: "SPARK" };

const two = (n: number) => String(n).padStart(2, "0");

/** ISO 8601 with the offset of the clock, since the rules on a sign are in local time. */
function localTime(clock: Date): string {
  const offset = -clock.getTimezoneOffset();
  const minutes = Math.abs(offset);
  const date = `${clock.getFullYear()}-${two(clock.getMonth() + 1)}-${two(clock.getDate())}`;
  const time = `${two(clock.getHours())}:${two(clock.getMinutes())}:${two(clock.getSeconds())}`;
  return `${date}T${time}${offset < 0 ? "-" : "+"}${two(Math.floor(minutes / 60))}:${two(minutes % 60)}`;
}

/** The context sent with a message. */
export function carContext(car: { distanceM: number; speedKmh: number }, clock: Date): ClientContext {
  return {
    car: { ...CAR, odometer_km: START_ODOMETER_KM + Math.floor(car.distanceM / 1000) },
    local_time: localTime(clock),
    speed_kmh: Math.round(car.speedKmh),
  };
}
