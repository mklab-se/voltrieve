/** A made up signature: a scribble drawn from a name. Nobody's handwriting is in it. */

export const SIGNATURE_BOX = { width: 600, height: 190 };
const MARGIN = 10;
/** A signature is shorter than a name can be. */
const LETTERS = 22;

type Point = readonly [x: number, y: number];
/** Where the pen goes for one letter, with the baseline at 0 and a small letter 1 high. `wide` is how
 *  far the next one starts. */
interface Shape {
  wide: number;
  points: readonly Point[];
}

const CAPITALS: readonly Shape[] = [
  // a stem and two bowls
  { wide: 2.2, points: [[0.1, 2.3], [0.5, 3.1], [0.5, 1.6], [0.25, -0.1], [-0.15, 0.2], [0.4, 2.2], [1.2, 3.2], [1.7, 2.5], [0.8, 1.6], [1.5, 1.1], [1.6, 0.2], [1.9, 0.1]] },
  // an open loop
  { wide: 2, points: [[1.5, 2.4], [1.4, 3.1], [0.8, 3], [0.15, 1.7], [0.3, 0.3], [1, -0.1], [1.8, 0.6]] },
  // two peaks
  { wide: 2.3, points: [[-0.1, 0], [0.3, 0.4], [1, 3.1], [1.2, 2.2], [1.3, 0.1], [1.1, 0.9], [0.3, 1.2], [0.5, 0.8], [1.5, 0.9], [2, 0.4]] },
  // an oval with a knot on top
  { wide: 2.2, points: [[1, 3], [0.2, 2.2], [0.1, 0.8], [0.8, -0.1], [1.6, 0.7], [1.7, 2.2], [1.1, 3.1], [0.8, 2.6], [1.3, 2.2], [2, 2.4]] },
  // a long bar and a stem that loops under the line
  { wide: 2, points: [[-0.1, 2.5], [0.6, 3.1], [1.9, 2.8], [1.2, 2.9], [0.9, 1.6], [0.6, -0.6], [0.1, -0.9], [0.1, -0.3], [1.4, 0.5]] },
  // a double curve
  { wide: 1.9, points: [[-0.2, 0.1], [0.7, 1.2], [1.4, 2.7], [1, 3.2], [0.6, 2.5], [1.2, 1.3], [1, 0.1], [0.2, -0.1], [0.1, 0.4], [1.5, 0.4]] },
];
const CAPITAL_OF: Record<string, number> = {};
["BDHKPRX", "CEGL", "AMNVW", "OQU", "FIJTYZ", "S"].forEach((letters, i) => [...letters].forEach((l) => (CAPITAL_OF[l] = i)));

const SMALL: readonly Shape[] = [
  // a wave
  { wide: 0.75, points: [[0.3, 0.95], [0.45, 0.1], [0.7, 0.15]] },
  // two waves
  { wide: 1.25, points: [[0.25, 0.95], [0.4, 0.05], [0.7, 0.85], [0.85, 0.05], [1.15, 0.2]] },
  // a loop
  { wide: 0.8, points: [[0.5, 0.6], [0.4, 1], [0.1, 0.6], [0.3, 0], [0.75, 0.25]] },
  // a bowl
  { wide: 0.95, points: [[0.6, 0.95], [0.2, 0.9], [0.1, 0.2], [0.5, 0.1], [0.65, 0.9], [0.65, 0.1], [0.9, 0.2]] },
  // a loop over the line
  { wide: 0.8, points: [[0.45, 1.3], [0.6, 2.4], [0.4, 2.6], [0.25, 1.4], [0.3, 0.1], [0.75, 0.3]] },
  // a loop under the line
  { wide: 0.95, points: [[0.55, 0.9], [0.15, 0.8], [0.2, 0.1], [0.6, 0.9], [0.5, -1], [0.2, -1.4], [0.1, -0.9], [0.85, 0.3]] },
];
const SMALL_OF: Record<string, number> = {};
["irsvcx", "mnuw", "e", "ao", "bdfhklt", "gjpqyz"].forEach((letters, i) => [...letters].forEach((l) => (SMALL_OF[l] = i)));

function hash(text: string): number {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.codePointAt(0)!, 16777619);
  return h >>> 0;
}

/** The same numbers for the same seed (mulberry32). */
function dice(seed: number): () => number {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function strokes(name: string): Point[] {
  const roll = dice(hash(name));
  const near = (n: number, by: number) => n + (roll() - 0.5) * by;
  // The hand: how much it leans and climbs, and how fast it gets lazy.
  const lean = near(0.34, 0.16);
  const climb = near(0.06, 0.06);
  const lazy = near(0.11, 0.06);

  const out: Point[] = [];
  let x = 0;
  const put = (px: number, py: number) => void out.push([px + py * lean, py + px * climb]);
  const words = name.normalize("NFD").replace(/\p{M}/gu, "").split(/[\s-]+/).filter(Boolean);
  let left = LETTERS;
  for (const word of words.length ? words : ["x"]) {
    if (left <= 0) break;
    [...word].slice(0, left).forEach((ch, i) => {
      left--;
      const big = i === 0 && (words.length < 3 || ch !== ch.toLowerCase());
      const key = big ? ch.toUpperCase() : ch.toLowerCase();
      const shapes = big ? CAPITALS : SMALL;
      const which = (big ? CAPITAL_OF : SMALL_OF)[key] ?? hash(key) % shapes.length;
      const { wide, points } = shapes[which];
      const tall = big ? 1 : Math.max(0.42, 1.05 - lazy * i);
      const broad = big ? 1 : Math.max(0.85, 1.15 - lazy * i * 0.5);
      for (const [px, py] of points) put(x + px * broad + near(0, 0.09), py * tall + near(0, 0.09));
      x += wide * broad;
    });
    x += 0.35;
  }
  // The flourish: out to the right, back under the name and out again.
  const end = x;
  put(end + near(0.7, 0.3), near(0.5, 0.2));
  put(end + near(1.6, 0.4), near(0.75, 0.3));
  put(end + near(1.5, 0.3), near(0.1, 0.2));
  put(end * 0.55, near(-0.75, 0.2));
  put(end * near(0.22, 0.1), near(-1.1, 0.2));
  put(end * 0.3, near(-1.45, 0.15));
  put(end * 0.75, near(-1.15, 0.2));
  put(end + near(1.4, 0.5), near(-0.6, 0.3));
  return out;
}

/** A smooth line through the points, with the control points of each piece. */
function curve(p: Point[]): Point[] {
  const all: Point[] = [p[0]];
  for (let i = 0; i + 1 < p.length; i++) {
    const a = p[Math.max(0, i - 1)];
    const [b, c] = [p[i], p[i + 1]];
    const d = p[Math.min(p.length - 1, i + 2)];
    all.push([b[0] + (c[0] - a[0]) / 6, b[1] + (c[1] - a[1]) / 6], [c[0] - (d[0] - b[0]) / 6, c[1] - (d[1] - b[1]) / 6], c);
  }
  return all;
}

/** One stroke as an SVG path in `SIGNATURE_BOX`, the same for the same name. */
export function signaturePath(name: string): string {
  const points = curve(strokes(name.normalize("NFC").trim().replace(/\s+/g, " ").slice(0, 200)));
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min((SIGNATURE_BOX.width - 2 * MARGIN) / (maxX - minX), (SIGNATURE_BOX.height - 2 * MARGIN) / (maxY - minY), 36);
  const down = (SIGNATURE_BOX.height - (maxY - minY) * scale) / 2;
  const at = ([x, y]: Point) => `${(MARGIN + (x - minX) * scale).toFixed(1)} ${(down + (maxY - y) * scale).toFixed(1)}`;
  const [start, ...rest] = points.map(at);
  let path = `M${start}`;
  for (let i = 0; i < rest.length; i += 3) path += ` C${rest[i]} ${rest[i + 1]} ${rest[i + 2]}`;
  return path;
}
