import { describe, expect, it } from "vitest";
import { SIGNATURE_BOX, signaturePath } from "./signature";

const numbers = (path: string) => path.match(/-?\d+(\.\d+)?/g)!.map(Number);

describe("a signature", () => {
  it("is the same for the same name, and another for another", () => {
    expect(signaturePath("Kim Berg")).toBe(signaturePath("Kim Berg"));
    expect(signaturePath("Kim Berg")).not.toBe(signaturePath("Anna Lind"));
    expect(signaturePath("Kim Berg")).not.toBe(signaturePath("Kim Borg"));
  });

  it("does not care how the letters are stored, or about spaces around them", () => {
    expect(signaturePath("Ödvar")).toBe(signaturePath("Ödvar"));
    expect(signaturePath("  Kim   Berg ")).toBe(signaturePath("Kim Berg"));
  });

  it.each(["", "   ", "Ö", "Kim Berg", "李雷", "😀", "1", "<script>", "x".repeat(5000), "a b ".repeat(90)])("is one stroke of path commands and numbers for %j", (name) => {
    const path = signaturePath(name);
    expect(path).toMatch(/^M-?[\d.]+ -?[\d.]+( ?C-?[\d.]+( -?[\d.]+){5})+$/);
    expect(path.match(/M/g)).toHaveLength(1);
    expect(path.length).toBeLessThan(12_000);
    expect(numbers(path).every(Number.isFinite)).toBe(true);
  });

  it.each(["", "Ö", "Kim Berg", "Anna-Karin von Lindqvist"])("stays in its box for %j", (name) => {
    const all = numbers(signaturePath(name));
    for (const [i, n] of all.entries()) {
      expect(n).toBeGreaterThanOrEqual(0);
      expect(n).toBeLessThanOrEqual(i % 2 === 0 ? SIGNATURE_BOX.width : SIGNATURE_BOX.height);
    }
  });
});
