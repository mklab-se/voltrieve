import { describe, expect, it } from "vitest";
import { BAYS_AT_M, BLOCK_M, blockOf, nextBay, passedSign, SIGN_AT_M, SIGN_IDS, signOf } from "./street";

describe("the street", () => {
  it("has blocks of 400 m with a sign at the start and several bays", () => {
    expect([BLOCK_M, SIGN_AT_M, BAYS_AT_M]).toEqual([400, 10, [60, 130, 200, 270, 340]]);
    expect([0, 399.9, 400, 4001].map(blockOf)).toEqual([0, 0, 1, 10]);
  });

  it("puts the signs up in a fixed order, never the same twice in a row", () => {
    expect(signOf(7)).toBe(signOf(7));
    expect(signOf(3)).not.toBe(signOf(4));
    for (let block = 0; block < 100; block += 1) expect(signOf(block)).not.toBe(signOf(block + 1));
    expect(new Set(SIGN_IDS).size).toBe(8);
    expect(Array.from({ length: 8 }, (_, i) => signOf(i))).toEqual([...SIGN_IDS]);
  });

  it("finds the next bay with room to stop", () => {
    expect(nextBay(0, 20)).toBe(60);
    expect(nextBay(40, 20)).toBe(60);
    expect(nextBay(40.1, 20)).toBe(130);
    expect(nextBay(335, 20)).toBe(400 + 60); // not enough room: the next block
    expect(nextBay(399, 500)).toBe(930); // more than a block
  });

  it("tells which sign was passed, once", () => {
    expect(passedSign(409, 411)).toBe(1);
    expect(passedSign(411, 420)).toBeUndefined();
    expect(passedSign(0, 9.9)).toBeUndefined();
    expect([passedSign(9, 10), passedSign(10, 11)]).toEqual([0, undefined]); // exactly on it
    expect(passedSign(9, 811)).toBe(2); // the last one
    expect(passedSign(410, 410)).toBeUndefined(); // standing still
  });
});
