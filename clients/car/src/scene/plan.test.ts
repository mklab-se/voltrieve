import { describe, expect, it } from "vitest";
import { BAYS_AT_M, BLOCK_M, SIGN_AT_M } from "../sim/street";
import { mayParkAt, planSegment, SEGMENT_M, type Side } from "./plan";

const segments = Array.from({ length: 200 }, (_, i) => i - 5);

describe("the plan of the street", () => {
  it("is the same each time", () => {
    expect(planSegment(17)).toEqual(planSegment(17));
    expect(planSegment(17)).not.toEqual(planSegment(18));
  });

  it("leaves the bays and the sign free", () => {
    for (const bay of BAYS_AT_M) expect(mayParkAt(3 * BLOCK_M + bay)).toBe(false);
    expect(mayParkAt(SIGN_AT_M)).toBe(false);
    for (const s of segments) {
      for (const car of planSegment(s).cars.filter((c) => c.x > 0)) {
        for (const bay of BAYS_AT_M) {
          const from = (((car.atM - bay + 200) % BLOCK_M) + BLOCK_M) % BLOCK_M - 200;
          expect(Math.abs(from)).toBeGreaterThan(10);
        }
      }
    }
  });

  it("has parked cars in every block", () => {
    for (let block = 0; block < 10; block += 1) {
      const cars = [...Array(10).keys()].flatMap((i) => planSegment(block * 10 + i).cars.filter((c) => c.x > 0));
      expect(cars.length).toBeGreaterThan(4);
    }
  });

  it.each(["left", "right"] as Side[])("never has two houses of one kind beside each other on the %s", (side) => {
    const all = segments.flatMap((s) => planSegment(s).houses.filter((h) => h.side === side));
    for (let i = 1; i < all.length; i += 1) expect(all[i]!.facade).not.toBe(all[i - 1]!.facade);
  });

  it.each(["left", "right"] as Side[])("fills the %s side of each segment from start to end", (side) => {
    for (const s of segments) {
      const row = planSegment(s).houses.filter((h) => h.side === side);
      expect(row[0]!.fromM).toBe(s * SEGMENT_M);
      const last = row.at(-1)!;
      expect(last.fromM + last.width).toBe((s + 1) * SEGMENT_M);
    }
  });

  it("puts nothing tall in front of a sign", () => {
    for (const s of segments) {
      for (const tree of planSegment(s).trees.filter((t) => t.x > 0)) {
        const rel = (((tree.atM - SIGN_AT_M) % BLOCK_M) + BLOCK_M) % BLOCK_M;
        expect(rel > 6 && rel < BLOCK_M - 44).toBe(true);
      }
    }
  });
});
