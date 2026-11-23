/** The textures of the street, most of them drawn by code. */
import {
  CanvasTexture,
  LinearMipmapLinearFilter,
  RepeatWrapping,
  SRGBColorSpace,
  TextureLoader,
  type Texture,
} from "three";
import { drawSign, type SignDef } from "../sim/signs";
import { FACADE_LEFT, FACADE_RIGHT, FACADES, KERB_LEFT, KERB_RIGHT, random, type Facade } from "./plan";

/** The road texture covers this much: the whole road across, and this far along. */
export const ROAD_LENGTH_M = 16;
export const PAVEMENT_LENGTH_M = 8;

export interface Textures {
  road: { map: Texture; bump: Texture };
  pavement: { map: Texture; bump: Texture };
  facades: Record<Facade, Texture>;
  leaves: Texture;
  skyline: Texture;
  /** A soft round spot, for the dark under a car. */
  spot: Texture;
  /** The front of a sign and its gray back. */
  sign(def: SignDef): { front: Texture; back: Texture };
}

function canvas(width: number, height: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  return [c, c.getContext("2d")!];
}

function texture(c: HTMLCanvasElement, color: boolean, repeat = false): CanvasTexture {
  const t = new CanvasTexture(c);
  if (color) t.colorSpace = SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = RepeatWrapping;
  t.minFilter = LinearMipmapLinearFilter;
  t.anisotropy = 16;
  return t;
}

const hsl = (h: number, s: number, l: number) => `hsl(${h.toFixed(1)} ${s.toFixed(1)}% ${l.toFixed(1)}%)`;

interface Area {
  x: number;
  width: number;
  stoneW: number;
  stoneH: number;
  hue: number;
  sat: number;
  light: number;
  spread: number;
}

/** Rows of rounded stones. The height picture gets the same stones, light in the middle. */
function setts(
  ctx: CanvasRenderingContext2D,
  bump: CanvasRenderingContext2D,
  height: number,
  area: Area,
  seed: number,
) {
  const rnd = random(seed);
  const rows = Math.round(height / area.stoneH);
  const rowH = height / rows;
  for (let row = 0; row < rows; row += 1) {
    let x = area.x - rnd() * area.stoneW;
    while (x < area.x + area.width) {
      const w = area.stoneW * (0.75 + rnd() * 0.55);
      const left = Math.max(x, area.x);
      const right = Math.min(x + w, area.x + area.width);
      if (right - left > 3) {
        const l = area.light + (rnd() - 0.5) * area.spread;
        const h = area.hue + (rnd() - 0.5) * 16;
        const y = row * rowH;
        const r = Math.min(rowH, right - left) * 0.28;
        ctx.fillStyle = hsl(h, area.sat * (0.5 + rnd()), l);
        ctx.beginPath();
        ctx.roundRect(left + 1, y + 1, right - left - 2, rowH - 2, r);
        ctx.fill();
        // The upper edge catches the light.
        ctx.fillStyle = hsl(h, area.sat * 0.6, l + 5);
        ctx.beginPath();
        ctx.roundRect(left + 2, y + 2, right - left - 5, rowH * 0.45, r);
        ctx.fill();
        const top = 165 + rnd() * 60;
        bump.fillStyle = `rgb(${top} ${top} ${top})`;
        bump.beginPath();
        bump.roundRect(left + 1.5, y + 1.5, right - left - 3, rowH - 3, r * 1.2);
        bump.fill();
      }
      x += w;
    }
  }
}

/** Large soft patches, so that the repeating is not seen. They wrap around the ends. */
function patches(ctx: CanvasRenderingContext2D, w: number, h: number, n: number, seed: number, strength: number) {
  const rnd = random(seed);
  for (let i = 0; i < n; i += 1) {
    const r = w * (0.06 + rnd() * 0.2);
    const [x, y] = [rnd() * w, rnd() * h];
    const dark = rnd() < 0.55;
    for (const dy of [-h, 0, h]) {
      const g = ctx.createRadialGradient(x, y + dy, 0, x, y + dy, r);
      const a = strength * (0.4 + rnd() * 0.6);
      g.addColorStop(0, dark ? `rgba(40,34,30,${a})` : `rgba(255,236,205,${a * 0.8})`);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y + dy - r, 2 * r, 2 * r);
    }
  }
}

function road(): Textures["road"] {
  const [w, h] = [1024, 2048];
  const [c, ctx] = canvas(w, h);
  const [b, bump] = canvas(w, h);
  const across = KERB_RIGHT - KERB_LEFT;
  const px = (m: number) => ((m - KERB_LEFT) / across) * w;
  const along = h / ROAD_LENGTH_M;
  ctx.fillStyle = "#57504a";
  ctx.fillRect(0, 0, w, h);
  bump.fillStyle = "#3a3a3a";
  bump.fillRect(0, 0, w, h);
  const strip = { hue: 24, sat: 6, light: 44, spread: 14, stoneW: 0.12 * along, stoneH: 0.125 * along };
  const lane = { hue: 32, sat: 9, light: 55, spread: 15, stoneW: 0.19 * along, stoneH: 0.15 * along };
  setts(ctx, bump, h, { x: 0, width: px(-4.75), ...strip }, 1);
  setts(ctx, bump, h, { x: px(-4.75), width: px(1.55) - px(-4.75), ...lane }, 2);
  setts(ctx, bump, h, { x: px(1.55), width: w - px(1.55), ...strip }, 3);
  patches(ctx, w, h, 46, 5, 0.17);
  // A little worn where the wheels go.
  for (const x of [-0.75, 0.75, -2.45, -3.95]) {
    const g = ctx.createLinearGradient(px(x - 0.35), 0, px(x + 0.35), 0);
    g.addColorStop(0, "rgba(30,26,24,0)");
    g.addColorStop(0.5, "rgba(30,26,24,0.16)");
    g.addColorStop(1, "rgba(30,26,24,0)");
    ctx.fillStyle = g;
    ctx.fillRect(px(x - 0.35), 0, px(x + 0.35) - px(x - 0.35), h);
  }
  const paint = (x: number, width: number, from: number, length: number, alpha: number) => {
    ctx.fillStyle = `rgba(244,240,230,${alpha})`;
    ctx.fillRect(px(x - width / 2), from * along, px(x + width / 2) - px(x - width / 2), length * along);
  };
  for (let m = 0; m < ROAD_LENGTH_M; m += 4) paint(-1.6, 0.13, m + 1, 1.7, 0.86);
  for (let m = 0; m < ROAD_LENGTH_M; m += 2) {
    paint(1.55, 0.09, m + 0.5, 1, 0.62);
    paint(-4.75, 0.09, m + 0.5, 1, 0.5);
  }
  // Worn paint: a few stones show through.
  const rnd = random(9);
  ctx.fillStyle = "rgba(120,108,98,0.55)";
  for (let i = 0; i < 500; i += 1) {
    const x = [-1.6, 1.55, -4.75][Math.floor(rnd() * 3)]!;
    ctx.fillRect(px(x - 0.07 + rnd() * 0.14), rnd() * h, 2 + rnd() * 4, 2 + rnd() * 5);
  }
  // Two covers.
  for (const [x, y] of [[0.1, 4.2], [-3.1, 12.4]] as const) {
    ctx.fillStyle = "#3f3a37";
    ctx.beginPath();
    ctx.ellipse(px(x), y * along, (0.32 / across) * w, 0.32 * along, 0, 0, 7);
    ctx.fill();
    ctx.strokeStyle = "#6c645e";
    ctx.lineWidth = 3;
    for (let r = 0.1; r < 0.3; r += 0.08) {
      ctx.beginPath();
      ctx.ellipse(px(x), y * along, (r / across) * w, r * along, 0, 0, 7);
      ctx.stroke();
    }
  }
  return { map: texture(c, true, true), bump: texture(b, false, true) };
}

function pavement(): Textures["pavement"] {
  const [w, h] = [512, 1024];
  const [c, ctx] = canvas(w, h);
  const [b, bump] = canvas(w, h);
  ctx.fillStyle = "#6f675f";
  ctx.fillRect(0, 0, w, h);
  bump.fillStyle = "#444";
  bump.fillRect(0, 0, w, h);
  const small = { hue: 26, sat: 6, light: 46, spread: 13, stoneW: 15, stoneH: 15 };
  setts(ctx, bump, h, { x: 0, width: w, ...small }, 21);
  // Two rows of large granite slabs.
  const rnd = random(31);
  for (const x of [w * 0.2, w * 0.52]) {
    for (let y = 0; y < h; y += 128) {
      const l = 60 + rnd() * 8;
      ctx.fillStyle = hsl(30, 7, l);
      ctx.fillRect(x + 1.5, y + 1.5, w * 0.31, 125);
      bump.fillStyle = "#d0d0d0";
      bump.fillRect(x + 1.5, y + 1.5, w * 0.31, 125);
      for (let i = 0; i < 260; i += 1) {
        ctx.fillStyle = `rgba(${rnd() < 0.5 ? "60,52,48" : "255,245,230"},0.16)`;
        ctx.fillRect(x + 2 + rnd() * w * 0.3, y + 2 + rnd() * 123, 2, 2);
      }
    }
  }
  patches(ctx, w, h, 22, 41, 0.15);
  return { map: texture(c, true, true), bump: texture(b, false, true) };
}

/** A cluster of small leaves on a clear ground. */
function leaves(): Texture {
  const [c, ctx] = canvas(256, 256);
  const rnd = random(77);
  for (let i = 0; i < 190; i += 1) {
    const a = rnd() * 6.28;
    const r = Math.sqrt(rnd()) * 108;
    const [x, y] = [128 + Math.cos(a) * r, 128 + Math.sin(a) * r];
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(rnd() * 6.28);
    ctx.fillStyle = hsl(66 + rnd() * 34, 24 + rnd() * 20, 22 + (i / 190) * 24 + rnd() * 9);
    ctx.beginPath();
    ctx.ellipse(0, 0, 11 + rnd() * 7, 5 + rnd() * 3, 0, 0, 7);
    ctx.fill();
    ctx.restore();
  }
  return texture(c, true);
}

/** Far away at the end of the street: water, hills and a church with a green spire. */
function skyline(): Texture {
  const [w, h] = [1024, 512];
  const [c, ctx] = canvas(w, h);
  const hill = (y: number, amp: number, color: string, seed: number) => {
    const rnd = random(seed);
    const [p1, p2] = [rnd() * 6, rnd() * 6];
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let x = 0; x <= w; x += 4) {
      ctx.lineTo(x, y - amp * (0.5 + 0.3 * Math.sin(x * 0.011 + p1) + 0.2 * Math.sin(x * 0.034 + p2)));
    }
    ctx.lineTo(w, h);
    ctx.fill();
  };
  hill(452, 46, "rgba(150,172,190,0.75)", 3);
  hill(466, 30, "rgba(126,152,150,0.8)", 4);
  ctx.fillStyle = "rgba(206,222,232,1)";
  ctx.fillRect(0, 462, w, 50);
  const rnd = random(8);
  for (let x = 300; x < 730; ) {
    const bw = 14 + rnd() * 26;
    const bh = 16 + rnd() * 26;
    ctx.fillStyle = hsl(28 + rnd() * 18, 38, 66 + rnd() * 10);
    ctx.fillRect(x, 466 - bh, bw, bh);
    ctx.fillStyle = hsl(16, 24, 48);
    ctx.fillRect(x - 1, 463 - bh, bw + 2, 5);
    x += bw + (rnd() < 0.2 ? 8 : 0);
  }
  // The church.
  ctx.fillStyle = "#d9a878";
  ctx.fillRect(496, 372, 34, 94);
  ctx.fillStyle = "#8fb0a2";
  ctx.beginPath();
  ctx.moveTo(492, 374);
  ctx.lineTo(500, 344);
  ctx.lineTo(506, 330);
  ctx.lineTo(513, 236);
  ctx.lineTo(520, 330);
  ctx.lineTo(526, 344);
  ctx.lineTo(534, 374);
  ctx.fill();
  ctx.strokeStyle = "#6f8d82";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(513, 240);
  ctx.lineTo(513, 218);
  ctx.stroke();
  return texture(c, true);
}

function spot(): Texture {
  const [c, ctx] = canvas(128, 128);
  const g = ctx.createRadialGradient(64, 64, 6, 64, 64, 64);
  g.addColorStop(0, "rgba(16,13,12,0.85)");
  g.addColorStop(0.62, "rgba(16,13,12,0.62)");
  g.addColorStop(1, "rgba(16,13,12,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return texture(c, true);
}

function facade(name: Facade): Texture {
  const t = new TextureLoader().load(`${import.meta.env.BASE_URL}facades/${name}.webp`);
  t.colorSpace = SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function sign(def: SignDef): { front: Texture; back: Texture } {
  const front = drawSign(def, 96);
  const [back, ctx] = canvas(front.width, front.height);
  ctx.drawImage(front, 0, 0);
  ctx.globalCompositeOperation = "source-in";
  ctx.fillStyle = "#8d9297";
  ctx.fillRect(0, 0, back.width, back.height);
  return { front: texture(front, true), back: texture(back, true) };
}

export function makeTextures(): Textures {
  return {
    road: road(),
    pavement: pavement(),
    facades: Object.fromEntries(FACADES.map((name) => [name, facade(name)])) as Textures["facades"],
    leaves: leaves(),
    skyline: skyline(),
    spot: spot(),
    sign,
  };
}

export const ROAD_WIDTH_M = KERB_RIGHT - KERB_LEFT;
export const PAVEMENT_WIDTHS_M = { left: KERB_LEFT - FACADE_LEFT, right: FACADE_RIGHT - KERB_RIGHT };
