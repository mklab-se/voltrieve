/** What the displays show, in steps that can be read. Pure. */
import { TOP_KMH, type Car } from "./sim/car";
import { BLOCK_M, blockOf, SIGN_AT_M } from "./sim/street";
import type { WiperMode } from "./tools";

/** Made up: a full battery takes the Spark 310 km, and the trip starts at 80 percent. */
const FULL_RANGE_KM = 310;
const START_RANGE_KM = 248;

export interface Gauges {
  speed: number;
  gear: "P" | "D";
  rangeKm: number;
  batteryPercent: number;
  wipers: WiperMode;
  /** The block of the last sign, when one has been passed. */
  block?: number;
  signInM: number;
  signAgoM?: number;
}

/** The block whose sign was passed last. "Here" means this block. */
export function lastSignBlock(distanceM: number): number | undefined {
  return distanceM < SIGN_AT_M ? undefined : blockOf(distanceM - SIGN_AT_M);
}

export function readGauges(car: Car, wipers: WiperMode): Gauges {
  const block = lastSignBlock(car.distanceM);
  const next = (block === undefined ? 0 : block + 1) * BLOCK_M + SIGN_AT_M;
  const rangeKm = Math.max(START_RANGE_KM - Math.floor(car.distanceM / 1000), 0);
  const stands = car.speedKmh === 0 && (car.targetKmh === 0 || car.mode === "parked");
  return {
    speed: Math.min(Math.round(car.speedKmh), TOP_KMH),
    gear: stands ? "P" : "D",
    rangeKm,
    batteryPercent: Math.round((100 * rangeKm) / FULL_RANGE_KM),
    wipers,
    block,
    signInM: Math.ceil((next - car.distanceM) / 10) * 10,
    signAgoM: block === undefined ? undefined : Math.floor((car.distanceM - (block * BLOCK_M + SIGN_AT_M)) / 10) * 10,
  };
}
