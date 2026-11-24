/** What stands along the street, decided from the segment alone, so it is the same each time. Pure. */
import { BAYS_AT_M, BLOCK_M, SIGN_AT_M } from "../sim/street";

export const SEGMENT_M = 40;

/** Across the street, in meters from the middle of the car's lane. Right is positive. */
export const KERB_RIGHT = 3.7;
export const FACADE_RIGHT = 6.6;
export const KERB_LEFT = -6.8;
export const FACADE_LEFT = -9.5;
export const STRIP_RIGHT = 2.65;
export const STRIP_LEFT = -5.75;
export const SIGN_X = 4.35;

export const FACADES = ["ochre", "red", "cream", "cafe", "salmon"] as const;
export type Facade = (typeof FACADES)[number];
export const CAR_MODELS = 3;
export const CAR_COLORS = 8;
export const ROOF_COLORS = 4;

export type Side = "left" | "right";

export interface House {
  side: Side;
  facade: Facade;
  /** Where it starts, along the street. */
  fromM: number;
  width: number;
  height: number;
  roof: number;
}

export interface Thing {
  atM: number;
  x: number;
  turn: number;
  scale: number;
}

export interface SegmentPlan {
  houses: House[];
  cars: (Thing & { model: number; color: number })[];
  trees: (Thing & { tint: number })[];
  lamps: Thing[];
  planters: Thing[];
  bikes: Thing[];
}

/** A small random generator (mulberry32). */
export function random(seed: number): () => number {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const STALL_M = 6.6;
const STALLS = 6;
/** Room for the car to swing in and out of a bay, from its middle to the middle of a parked car. */
const BAY_BEFORE_M = 11.2;
const BAY_AFTER_M = 11.7;
const SIGN_CLEAR_M = 9;

const mod = (n: number, m: number) => ((n % m) + m) % m;

/** Meters from the nearest thing of its kind: negative is before it. */
function fromNearest(atM: number, places: readonly number[]): number {
  const rel = mod(atM, BLOCK_M);
  const all = places.flatMap((p) => [rel - p, rel - p - BLOCK_M, rel - p + BLOCK_M]);
  return all.reduce((a, b) => (Math.abs(b) < Math.abs(a) ? b : a));
}

export function mayParkAt(atM: number): boolean {
  const bay = fromNearest(atM, BAYS_AT_M);
  if (bay > -BAY_BEFORE_M && bay < BAY_AFTER_M) return false;
  return Math.abs(fromNearest(atM, [SIGN_AT_M])) > SIGN_CLEAR_M;
}

/** Widths along 40 m. A space of 3 m is an alley that lets the sun in. */
const WIDTHS = [
  [13, 14, 13],
  [14, 12, 14],
  [12, 15, 13],
  [14.5, 12.5, 13],
];
const WIDTHS_WITH_ALLEY = [
  [12.5, -3, 12, 12.5],
  [12, 12.5, -3, 12.5],
];

function houses(segment: number, side: Side): House[] {
  const rnd = random(segment * 7919 + (side === "left" ? 17 : 5003));
  const alley = rnd() < (side === "left" ? 0.4 : 0.15);
  const choices = alley ? WIDTHS_WITH_ALLEY : WIDTHS;
  const widths = choices[Math.floor(rnd() * choices.length)]!;
  // Each step is 1 to 4 places on in the order of the five, so neighbors in a segment differ.
  let facade = Math.floor(rnd() * FACADES.length);
  const result: House[] = [];
  let fromM = segment * SEGMENT_M;
  for (const width of widths) {
    if (width > 0) {
      facade = (facade + 1 + Math.floor(rnd() * 2)) % FACADES.length;
      const height = 13 + rnd() * 2.6;
      result.push({ side, facade: FACADES[facade]!, fromM, width, height, roof: Math.floor(rnd() * ROOF_COLORS) });
    }
    fromM += Math.abs(width);
  }
  // The first house also differs from the last one before it: segments take turns with a fixed color.
  const first = result[0]!;
  if (mod(segment, 2) === 0) first.facade = "ochre";
  else first.facade = side === "left" ? "red" : "cream";
  for (let i = 1; i < result.length; i += 1) {
    const last = i === result.length - 1;
    const next = mod(segment, 2) === 1 ? "ochre" : side === "left" ? "red" : "cream";
    while (result[i]!.facade === result[i - 1]!.facade || (last && result[i]!.facade === next)) {
      result[i]!.facade = FACADES[(FACADES.indexOf(result[i]!.facade) + 1) % FACADES.length]!;
    }
  }
  return result;
}

export function planSegment(segment: number): SegmentPlan {
  const rnd = random(segment * 104729 + 11);
  const start = segment * SEGMENT_M;
  const plan: SegmentPlan = {
    houses: [...houses(segment, "left"), ...houses(segment, "right")],
    cars: [],
    trees: [],
    lamps: [],
    planters: [],
    bikes: [],
  };
  for (let i = 0; i < STALLS; i += 1) {
    const atM = start + STALL_M * (i + 0.5);
    for (const side of ["right", "left"] as const) {
      const [taken, model, color, nudge, turn] = [rnd(), rnd(), rnd(), rnd(), rnd()];
      const free = taken > 0.74 || (side === "right" && !mayParkAt(atM));
      if (free) continue;
      plan.cars.push({
        atM: atM + (nudge - 0.5) * 0.5,
        x: (side === "right" ? STRIP_RIGHT + 0.06 : STRIP_LEFT - 0.06) + (nudge - 0.5) * 0.16,
        turn: (side === "right" ? 0 : Math.PI) + (turn - 0.5) * 0.04,
        scale: 1,
        model: Math.floor(model * CAR_MODELS),
        color: Math.floor(color * CAR_COLORS),
      });
    }
  }

  // Nothing tall near the sign, so it can be seen from far away.
  const signFree = (atM: number) => {
    const from = fromNearest(atM, [SIGN_AT_M]);
    return from < -44 || from > 6;
  };
  for (const side of ["right", "left"] as const) {
    const x = side === "right" ? KERB_RIGHT + 0.55 : KERB_LEFT - 0.55;
    const tree = start + 11 + rnd() * 4;
    const [has, scale, turn, tint] = [rnd(), rnd(), rnd(), rnd()];
    if (has < 0.62 && (side === "left" || signFree(tree))) {
      plan.trees.push({ atM: tree, x, turn: turn * 6.28, scale: 0.85 + scale * 0.35, tint });
    }
    plan.lamps.push({ atM: start + (side === "right" ? 30 : 34), x: side === "right" ? x - 0.1 : x + 0.1, turn: 0, scale: 1 });
  }
  for (const house of plan.houses) {
    const wall = house.side === "right" ? FACADE_RIGHT - 0.55 : FACADE_LEFT + 0.55;
    const near = Math.abs(fromNearest(house.fromM + house.width / 2, [SIGN_AT_M])) < 14 && house.side === "right";
    if (near) continue;
    if (house.facade === "cafe" || rnd() < 0.3) {
      for (const along of [0.06, 0.94]) {
        plan.planters.push({ atM: house.fromM + house.width * along, x: wall, turn: rnd() * 6.28, scale: 0.9 + rnd() * 0.3 });
      }
    }
    if (house.facade !== "cafe" && rnd() < 0.35) {
      const atM = house.fromM + house.width * (0.15 + rnd() * 0.1);
      for (let i = 0; i < 2; i += 1) {
        plan.bikes.push({ atM: atM + i * 0.75, x: wall + (house.side === "right" ? -0.1 : 0.1), turn: 0.25 + rnd() * 0.2, scale: 1 });
      }
    }
  }
  return plan;
}
