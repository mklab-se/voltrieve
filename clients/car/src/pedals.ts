/** What the two pedals do. Held, they change the speed until they are let go. A tap is a small step. */
import { TOP_KMH } from "./sim/car";

export type Pedal = "accelerate" | "brake";

const TAP_MS = 200;
const TAP_KMH = 5;
/** Taps this close to each other add up, although the car has not reached the last one yet. */
const SERIES_MS = 1500;

export class Pedals {
  private down?: { pedal: Pedal; atMs: number };
  private lastTap?: { kmh: number; atMs: number };

  constructor(private readonly car: { speedKmh(): number; setSpeed(kmh: number): void }) {}

  press(pedal: Pedal, nowMs: number): void {
    if (this.down?.pedal === pedal) return;
    this.down = { pedal, atMs: nowMs };
    this.car.setSpeed(pedal === "accelerate" ? TOP_KMH : 0);
  }

  release(pedal: Pedal, nowMs: number): void {
    if (this.down?.pedal !== pedal) return;
    const tapped = nowMs - this.down.atMs < TAP_MS;
    this.down = undefined;
    this.car.setSpeed(tapped ? this.tap(pedal, nowMs) : Math.round(this.car.speedKmh()));
  }

  releaseAll(nowMs: number): void {
    if (this.down) this.release(this.down.pedal, nowMs);
  }

  private tap(pedal: Pedal, nowMs: number): number {
    const series = this.lastTap && nowMs - this.lastTap.atMs < SERIES_MS;
    const steps = (series ? this.lastTap!.kmh : this.car.speedKmh()) / TAP_KMH;
    const next = pedal === "accelerate" ? Math.floor(steps + 1) : Math.ceil(steps - 1);
    const kmh = Math.min(Math.max(next * TAP_KMH, 0), TOP_KMH);
    this.lastTap = { kmh, atMs: nowMs };
    return kmh;
  }
}
