import type { ClientContext } from "./types";

/** The customer's car: made up, and the same one as in the car client. */
export interface Car {
  model: "Voltrieve Spark";
  trim: "Prime";
  drive: "RWD";
  model_year: 2026;
  registration: "SPARK";
  odometer_km: 12400;
  color: "Saffron";
  nextService: "March 2028 or 30,000 km";
}

export const MY_CAR: Car = {
  model: "Voltrieve Spark",
  trim: "Prime",
  drive: "RWD",
  model_year: 2026,
  registration: "SPARK",
  odometer_km: 12400,
  color: "Saffron",
  nextService: "March 2028 or 30,000 km",
};

/** ISO 8601 with the offset of the browser, which toISOString leaves out. */
function localTime(now: Date): string {
  const offset = -now.getTimezoneOffset();
  const clock = new Date(now.getTime() + offset * 60_000).toISOString().slice(0, 19);
  const two = (n: number) => String(n).padStart(2, "0");
  const minutes = Math.abs(offset);
  return `${clock}${offset < 0 ? "-" : "+"}${two(Math.floor(minutes / 60))}:${two(minutes % 60)}`;
}

/** What goes with each question. The API takes these fields and no others. */
export function webContext(now: Date): ClientContext {
  const { model, trim, drive, model_year, registration, odometer_km } = MY_CAR;
  return { car: { model, trim, drive, model_year, registration, odometer_km }, local_time: localTime(now) };
}
