/** Swedish parking signs, drawn from a definition, so that the text is right and the answer is known. */
import signs from "./signs.json";
import type { SignId } from "./street";

type NoParking = "no_parking" | "no_stopping";

export interface SignDef {
  id: string;
  top: "P" | NoParking;
  /** Rows in parentheses are for days before a red day, red rows for red days. */
  panels: { color: "blue" | "yellow"; symbol?: NoParking; rows: { text: string; red?: boolean }[] }[];
}

/** In the order of the street. */
export const SIGNS = signs as SignDef[];

export function signDef(id: SignId): SignDef {
  return SIGNS.find((s) => s.id === id)!;
}

const BLUE = "#00539b";
const YELLOW = "#ffcd00";
const RED = "#c8102e";
const WHITE = "#ffffff";
const BLACK = "#111111";

// All in row heights.
const TOP = 4.5;
const SYMBOL = 2;
const PADDING = 0.45;
const GAP = 0.25;
const BORDER = 0.1;
const CHARACTER = 0.62; // the width of one, about, in bold

interface Square {
  x: number;
  y: number;
  size: number;
}

export interface PanelLayout {
  y: number;
  width: number;
  height: number;
  fill: string;
  border: string;
  symbol?: Square & { kind: NoParking };
  /** `fill` is the strip behind a red row: red on blue cannot be read. */
  rows: { text: string; y: number; height: number; ink: string; fill?: string }[];
}

export interface SignLayout {
  rowPx: number;
  width: number;
  height: number;
  top: Square & { kind: SignDef["top"] };
  panels: PanelLayout[];
}

/** Where everything goes, in whole pixels, with rows that are `rowPx` high. */
export function layoutSign(def: SignDef, rowPx: number): SignLayout {
  const px = (rows: number) => Math.round(rows * rowPx);
  const longest = Math.max(0, ...def.panels.flatMap((p) => p.rows.map((r) => r.text.length)));
  const width = Math.max(px(TOP), px(longest * CHARACTER + 2 * PADDING));
  const top = { kind: def.top, x: Math.round((width - px(TOP)) / 2), y: 0, size: px(TOP) };
  let y = top.size;
  const panels = def.panels.map((panel): PanelLayout => {
    const start = y;
    const blue = panel.color === "blue";
    y += px(PADDING);
    const symbol = panel.symbol && { kind: panel.symbol, x: Math.round((width - px(SYMBOL)) / 2), y, size: px(SYMBOL) };
    if (symbol) y += symbol.size + px(GAP);
    const rows = panel.rows.map(({ text, red }, i) => {
      if (i > 0) y += px(GAP);
      const row = { text, y, height: rowPx, ink: red ? RED : blue ? WHITE : BLACK, fill: red ? WHITE : undefined };
      y += rowPx;
      return row;
    });
    y += px(PADDING);
    return { y: start, width, height: y - start, fill: blue ? BLUE : YELLOW, border: blue ? WHITE : RED, symbol, rows };
  });
  return { rowPx, width, height: y, top, panels };
}

export function drawSign(def: SignDef, rowPx: number): HTMLCanvasElement {
  const layout = layoutSign(def, rowPx);
  const canvas = document.createElement("canvas");
  canvas.width = layout.width;
  canvas.height = layout.height;
  const ctx = canvas.getContext("2d")!;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const { top } = layout;
  if (top.kind === "P") drawP(ctx, top);
  else drawDisc(ctx, top, top.kind);
  for (const panel of layout.panels) drawPanel(ctx, panel, rowPx);
  return canvas;
}

const font = (px: number) => `700 ${px}px "Helvetica Neue", Helvetica, Arial, sans-serif`;

/** A filled box with a border just inside its edge. */
function drawBox(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, line: number, fill: string, border: string) {
  ctx.fillStyle = fill;
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = border;
  ctx.lineWidth = line;
  ctx.strokeRect(x + line, y + line, w - 2 * line, h - 2 * line);
}

function drawP(ctx: CanvasRenderingContext2D, { x, y, size }: Square) {
  drawBox(ctx, x, y, size, size, Math.max(1, Math.round(size * 0.025)), BLUE, WHITE);
  ctx.fillStyle = WHITE;
  ctx.font = font(Math.round(size * 0.8));
  ctx.fillText("P", x + size / 2, y + size * 0.53);
}

/** Blue with a red ring and one red bar for no parking, two for no stopping. */
function drawDisc(ctx: CanvasRenderingContext2D, { x, y, size }: Square, kind: NoParking) {
  // A thin white edge: red and blue are about as dark as each other, so a photo has few edges without it.
  const r = size / 2;
  ctx.fillStyle = WHITE;
  ctx.beginPath();
  ctx.arc(x + r, y + r, r, 0, 2 * Math.PI);
  ctx.fill();
  drawInnerDisc(ctx, x + r, y + r, r - Math.max(1, Math.round(size * 0.02)), kind);
}

function drawInnerDisc(ctx: CanvasRenderingContext2D, cx: number, cy: number, r: number, kind: NoParking) {
  const line = r * 0.2;
  ctx.fillStyle = BLUE;
  ctx.strokeStyle = RED;
  ctx.lineWidth = line;
  ctx.beginPath();
  ctx.arc(cx, cy, r - line / 2, 0, 2 * Math.PI);
  ctx.fill();
  ctx.stroke();
  const d = (r - line) * Math.SQRT1_2;
  ctx.beginPath();
  ctx.moveTo(cx - d, cy - d);
  ctx.lineTo(cx + d, cy + d);
  if (kind === "no_stopping") {
    ctx.moveTo(cx + d, cy - d);
    ctx.lineTo(cx - d, cy + d);
  }
  ctx.stroke();
}

function drawPanel(ctx: CanvasRenderingContext2D, panel: PanelLayout, rowPx: number) {
  const line = Math.max(1, Math.round(rowPx * BORDER));
  const inner = panel.width - 4 * line;
  drawBox(ctx, 0, panel.y, panel.width, panel.height, line, panel.fill, panel.border);
  if (panel.symbol) drawDisc(ctx, panel.symbol, panel.symbol.kind);
  ctx.font = font(rowPx);
  for (const row of panel.rows) {
    if (row.fill) {
      ctx.fillStyle = row.fill;
      ctx.fillRect(2 * line, row.y, inner, row.height);
    }
    ctx.fillStyle = row.ink;
    ctx.fillText(row.text, panel.width / 2, row.y + row.height / 2, inner);
  }
}
