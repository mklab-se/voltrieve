import { afterEach, describe, expect, it, vi } from "vitest";
import { drawSign, layoutSign, signDef, SIGNS } from "./signs";
import { SIGN_IDS } from "./street";

const ROW = 40;
const rowsOf = (id: (typeof SIGN_IDS)[number]) =>
  signDef(id).panels.map((p) => [p.color, ...p.rows.map((r) => (r.red ? `red ${r.text}` : r.text))]);

/** A 2D context that remembers the texts it drew. */
function fakeCanvas() {
  const texts: { text: string; x: number; y: number; font: string; color: unknown }[] = [];
  const state: Record<string, unknown> = {};
  const context = new Proxy(state, {
    get: (target, key: string) =>
      key === "fillText"
        ? (text: string, x: number, y: number) => texts.push({ text, x, y, font: String(target.font), color: target.fillStyle })
        : (target[key] ?? (() => {})),
    set: (target, key: string, value) => ((target[key] = value), true),
  });
  return { width: 0, height: 0, getContext: () => context, texts };
}

afterEach(() => vi.unstubAllGlobals());

describe("the sign definitions", () => {
  it("are the signs of the street, in the same order", () => {
    expect(SIGNS.map((s) => s.id)).toEqual([...SIGN_IDS]);
  });

  it("say what the design says", () => {
    expect(SIGNS.map((s) => s.top)).toEqual(["P", "P", "P", "P", "no_parking", "P", "no_stopping", "P"]);
    expect(SIGN_IDS.map(rowsOf)).toEqual([
      [["blue", "2 tim", "9-18", "(9-15)"], ["blue", "Avgift", "7-19", "(11-17)"]],
      [["blue", "Avgift"], ["blue", "2 tim", "8-18", "(8-11)", "red 8-15"]],
      [["blue", "2 tim"], ["blue", "Avgift", "8-18"]],
      [["blue", "2 tim", "Avgift", "8-18"]],
      [["yellow", "Fredag", "8-16", "1/11-15/5"]],
      [["blue", "Avgift", "8-18"], ["yellow", "Måndag", "12-15", "1 okt-30 april"]],
      [],
      [["blue", "Endast", "laddning"]],
    ]);
    expect(SIGNS.flatMap((s) => s.panels.filter((p) => p.symbol).map(() => s.id))).toEqual(["cleaning"]);
  });
});

describe.each(SIGNS)("the layout of $id", (def) => {
  const layout = layoutSign(def, ROW);

  it("is as high as the top sign and its panels", () => {
    const panels = layout.panels.reduce((sum, p) => sum + p.height, 0);
    expect(layout.height).toBe(layout.top.size + panels);
    expect(layout.panels).toHaveLength(def.panels.length);
    let y = layout.top.size;
    for (const panel of layout.panels) {
      expect([panel.y, panel.width]).toEqual([y, layout.width]);
      y += panel.height;
    }
  });

  it("has its rows at the height that was asked for, inside the panel and not over each other", () => {
    layout.panels.forEach((panel, i) => {
      expect(panel.rows.map((r) => r.text)).toEqual(def.panels[i]!.rows.map((r) => r.text));
      let from = panel.y + (panel.symbol ? panel.symbol.y - panel.y + panel.symbol.size : 0);
      for (const row of panel.rows) {
        expect(row.height).toBe(ROW);
        expect(row.y).toBeGreaterThanOrEqual(from);
        from = row.y + row.height;
      }
      expect(from).toBeLessThan(panel.y + panel.height);
    });
  });

  it("is wide enough for its longest row and for the top sign", () => {
    expect(layout.width).toBeGreaterThanOrEqual(layout.top.size);
    const longest = Math.max(0, ...def.panels.flatMap((p) => p.rows.map((r) => r.text.length)));
    expect(layout.width).toBeGreaterThanOrEqual(longest * ROW * 0.6);
  });

  it("draws, with every row in its place", () => {
    const canvas = fakeCanvas();
    vi.stubGlobal("document", { createElement: () => canvas });
    expect(drawSign(def, ROW)).toBe(canvas);
    expect([canvas.width, canvas.height]).toEqual([layout.width, layout.height]);
    const rows = layout.panels.flatMap((p) => p.rows);
    const drawn = canvas.texts.filter((t) => t.text !== "P");
    expect(drawn.map((t) => t.text)).toEqual(rows.map((r) => r.text));
    expect(drawn.map((t) => t.y)).toEqual(rows.map((r) => r.y + ROW / 2));
    for (const t of drawn) expect(t.font).toContain(`${ROW}px`);
    expect(canvas.texts.length - drawn.length).toBe(def.top === "P" ? 1 : 0);
  });
});

describe("the colors", () => {
  const panel = (id: (typeof SIGN_IDS)[number], i: number) => layoutSign(signDef(id), ROW).panels[i]!;

  it("are white on blue, black on yellow with a red border, and red for red days", () => {
    const blue = panel("cleaning", 0);
    const yellow = panel("cleaning", 1);
    const [time, red] = panel("fee_always_two_hours", 1).rows.slice(-2);
    expect(new Set(blue.rows.map((r) => r.ink))).toEqual(new Set(["#ffffff"]));
    expect(new Set(yellow.rows.map((r) => r.ink))).toEqual(new Set(["#111111"]));
    expect(yellow.border).toBe(red!.ink);
    expect(blue.border).toBe("#ffffff");
    expect(red!.ink).not.toBe(time!.ink);
    expect([time!.fill, red!.fill]).toEqual([undefined, "#ffffff"]); // red on blue cannot be read
    expect(blue.fill).not.toBe(yellow.fill);
  });

  it("grow with the row height, in whole pixels", () => {
    for (const row of [24, 33, 64]) {
      for (const def of SIGNS) {
        const { width, height, panels } = layoutSign(def, row);
        for (const n of [width, height, ...panels.flatMap((p) => p.rows.map((r) => r.y))]) expect(Number.isInteger(n)).toBe(true);
      }
    }
  });
});
