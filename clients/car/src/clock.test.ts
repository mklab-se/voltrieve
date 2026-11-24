import { describe, expect, it } from "vitest";
import { readClock } from "./components/Clock";

describe("the clock setter", () => {
  it("takes a day and a time", () => {
    expect(readClock("2026-03-06T10:15")).toEqual(new Date(2026, 2, 6, 10, 15));
  });

  it.each(["", "nonsense", "0002-03-06T10:15", "0202-03-06T10:15", "2100-01-01T00:00", "12026-01-01T00:00"])(
    "does not take %j, which is what a year looks like while it is typed",
    (value) => expect(readClock(value)).toBeUndefined(),
  );
});
