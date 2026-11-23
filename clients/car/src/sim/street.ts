/** The endless street: blocks with one parking rule each. Pure. */

export const BLOCK_M = 400;
/** Where the sign stands, from the start of the block. */
export const SIGN_AT_M = 10;
/** Where a parked car stands, from the start of the block. */
export const BAYS_AT_M = [60, 130, 200, 270, 340] as const;

/** The signs, in the order they come along the street. Each comes once, so neighbours differ. */
export const SIGN_IDS = [
  "two_hours_fee",
  "fee_always_two_hours",
  "two_hours_always_fee",
  "one_panel",
  "friday",
  "cleaning",
  "no_stopping",
  "charging",
] as const;

export type SignId = (typeof SIGN_IDS)[number];

export function blockOf(distanceM: number): number {
  return Math.floor(distanceM / BLOCK_M);
}

export function signOf(block: number): SignId {
  return SIGN_IDS[block % SIGN_IDS.length]!;
}

/** The first bay at least `needM` ahead, as a distance along the street. */
export function nextBay(distanceM: number, needM: number): number {
  const from = distanceM + needM;
  for (let block = blockOf(from); ; block += 1) {
    const bay = BAYS_AT_M.find((m) => block * BLOCK_M + m >= from);
    if (bay !== undefined) return block * BLOCK_M + bay;
  }
}

/** The block whose sign was passed between two places, the last one if there were several. */
export function passedSign(beforeM: number, afterM: number): number | undefined {
  const block = Math.floor((afterM - SIGN_AT_M) / BLOCK_M);
  return block >= 0 && block * BLOCK_M + SIGN_AT_M > beforeM ? block : undefined;
}
