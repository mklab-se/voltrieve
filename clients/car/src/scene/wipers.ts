/** Where the wipers are. Pure. A phase runs from 0 to 1 over one sweep up and down, and on through a pause. */
import type { WiperMode } from "../tools";

export interface Wipers {
  phase: number;
  /** The length of one round: 1 is the sweep, the rest is the pause at the bottom. */
  cycle: number;
  /** The seconds that the sweep takes. */
  sweepS: number;
}

const SPEEDS = { slow: { sweepS: 1.3, cycle: 2.1 }, fast: { sweepS: 0.75, cycle: 1 } } as const;

export const PARKED: Wipers = { phase: 0, cycle: SPEEDS.slow.cycle, sweepS: SPEEDS.slow.sweepS };

/** One step. When they are turned off, they finish the sweep and stay at the bottom. */
export function stepWipers(w: Wipers, mode: WiperMode, dtSeconds: number): Wipers {
  if (mode === "off") {
    if (w.phase === 0) return w;
    const phase = w.phase + dtSeconds / w.sweepS;
    return phase >= 1 ? { ...w, phase: 0 } : { ...w, phase };
  }
  const { sweepS, cycle } = SPEEDS[mode];
  const phase = Math.min(w.phase, cycle) + dtSeconds / sweepS;
  return { phase: phase >= cycle ? phase % cycle : phase, cycle, sweepS };
}

/** 0 at the bottom, 1 at the top. Slow at both ends, as a crank drives them. */
export function wiperPosition(phase: number): number {
  return phase >= 1 ? 0 : (1 - Math.cos(2 * Math.PI * phase)) / 2;
}
