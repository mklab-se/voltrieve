/** How the Spark moves. Pure: no clock and no randomness, only the step it is given. */
import { nextBay } from "./street";

export type Mode = "driving" | "parking" | "parked" | "leaving";

export interface Car {
  distanceM: number;
  speedKmh: number;
  targetKmh: number;
  mode: Mode;
  /** 0 in the lane, 1 in the bay. */
  lateral: number;
  /** The bay it parks in, as a distance along the street. */
  bayM?: number;
}

export const TOP_KMH = 150;
const ACCELERATION = 100 / 9.2; // km/h per second
const BRAKING = 25; // km/h per second
const CREEP_KMH = 15;
/** A hidden tab gets no frames. The car waits for it instead of jumping ahead. */
const MAX_STEP_S = 0.1;
/** The car moves sideways over this distance, at creep speed. */
const SIDEWAYS_M = 12;
/** Parking plans with less than the full brakes, so there is something left to correct with. */
const PLANNED_MS2 = (0.8 * BRAKING) / 3.6;

const ms = (kmh: number) => kmh / 3.6;

export function newCar(): Car {
  return { distanceM: 0, speedKmh: 0, targetKmh: 0, mode: "driving", lateral: 0 };
}

export function setSpeed(car: Car, kmh: number): Car {
  if (!Number.isFinite(kmh)) return car;
  const targetKmh = Math.min(Math.max(kmh, 0), TOP_KMH);
  if (car.mode === "parked" && targetKmh === 0) return car;
  if (car.lateral > 0) return { ...car, targetKmh, mode: "leaving", bayM: undefined };
  return { ...car, targetKmh, mode: "driving", bayM: undefined };
}

/** Park in the next bay there is room to stop for. From a standstill the car creeps to it. */
export function startParking(car: Car): Car {
  if (car.mode === "parked" || car.mode === "parking") return car;
  const targetKmh = Math.max(car.speedKmh, CREEP_KMH);
  const braking = (ms(targetKmh) ** 2 - ms(CREEP_KMH) ** 2) / (2 * PLANNED_MS2);
  return { ...car, mode: "parking", targetKmh, bayM: nextBay(car.distanceM, SIDEWAYS_M + braking) };
}

export function step(car: Car, dtSeconds: number): Car {
  const dt = dtSeconds > 0 ? Math.min(dtSeconds, MAX_STEP_S) : 0;
  if (dt === 0 || car.mode === "parked") return car;
  return car.mode === "parking" ? stepParking(car, dt) : stepDriving(car, dt);
}

function towards(speedKmh: number, targetKmh: number, dt: number): number {
  return targetKmh > speedKmh
    ? Math.min(speedKmh + ACCELERATION * dt, targetKmh)
    : Math.max(speedKmh - BRAKING * dt, targetKmh);
}

function stepDriving(car: Car, dt: number): Car {
  // A car that is half out of its bay gets all the way out, also when it is told to stop.
  const targetKmh = car.lateral > 0 ? Math.max(car.targetKmh, CREEP_KMH) : car.targetKmh;
  const speedKmh = towards(car.speedKmh, targetKmh, dt);
  const movedM = ms((car.speedKmh + speedKmh) / 2) * dt;
  const lateral = Math.max(car.lateral - movedM / SIDEWAYS_M, 0);
  const mode = lateral > 0 ? "leaving" : "driving";
  return { ...car, distanceM: car.distanceM + movedM, speedKmh, lateral, mode };
}

/** How fast the car may be this far from the bay: braking first, then creeping, then to a stop. */
function allowedKmh(leftM: number): number {
  const stopping = Math.sqrt(2 * PLANNED_MS2 * leftM) * 3.6;
  const beforeCreep = Math.max(leftM - SIDEWAYS_M, 0);
  const braking = Math.sqrt(ms(CREEP_KMH) ** 2 + 2 * PLANNED_MS2 * beforeCreep) * 3.6;
  return Math.min(stopping, braking);
}

function stepParking(car: Car, dt: number): Car {
  const bayM = car.bayM!;
  const leftM = bayM - car.distanceM;
  const speedKmh = towards(car.speedKmh, Math.min(car.targetKmh, allowedKmh(leftM)), dt);
  const movedM = ms((car.speedKmh + speedKmh) / 2) * dt;
  if (movedM >= leftM - 0.001) {
    return { distanceM: bayM, speedKmh: 0, targetKmh: 0, mode: "parked", lateral: 1, bayM };
  }
  // Out of the old bay when it is still in one, then into the new one.
  const out = car.lateral - movedM / SIDEWAYS_M;
  const into = 1 - (leftM - movedM) / SIDEWAYS_M;
  return { ...car, distanceM: car.distanceM + movedM, speedKmh, lateral: Math.max(out, into, 0) };
}
