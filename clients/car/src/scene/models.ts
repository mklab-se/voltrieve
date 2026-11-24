/** The shapes of what stands along the street. Each is one or two merged geometries, colored by vertex. */
import {
  BoxGeometry,
  BufferGeometry,
  Color,
  CylinderGeometry,
  ExtrudeGeometry,
  Float32BufferAttribute,
  Matrix4,
  PlaneGeometry,
  Quaternion,
  Shape,
  SphereGeometry,
  TorusGeometry,
  Vector3,
} from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { random } from "./plan";

type Place = { at?: [number, number, number]; turn?: [number, number, number] };

function part(geometry: BufferGeometry, color: string, { at = [0, 0, 0], turn = [0, 0, 0] }: Place = {}): BufferGeometry {
  const g = geometry.index ? geometry.toNonIndexed() : geometry;
  g.rotateX(turn[0]).rotateY(turn[1]).rotateZ(turn[2]).translate(...at);
  const c = new Color(color);
  const n = g.getAttribute("position").count;
  const colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i += 1) c.toArray(colors, i * 3);
  g.setAttribute("color", new Float32BufferAttribute(colors, 3));
  return g;
}

const merge = (parts: BufferGeometry[]) => mergeGeometries(parts)!;
const box = (w: number, h: number, d: number) => new BoxGeometry(w, h, d);
const tube = (r: number, length: number, top = r, sides = 10) => new CylinderGeometry(top, r, length, sides);

/** A tube between two points. */
function rod(from: [number, number, number], to: [number, number, number], r: number, color: string): BufferGeometry {
  const [a, b] = [new Vector3(...from), new Vector3(...to)];
  const dir = b.clone().sub(a);
  const g = tube(r, dir.length(), r, 6);
  g.applyMatrix4(
    new Matrix4().compose(a.add(b).multiplyScalar(0.5), new Quaternion().setFromUnitVectors(new Vector3(0, 1, 0), dir.normalize()), new Vector3(1, 1, 1)),
  );
  return part(g, color);
}

/** An outline in the side view, pushed out to a width, with round edges. The front is at negative z. */
function slab(outline: [number, number][], width: number, round: number): BufferGeometry {
  const shape = new Shape();
  // Corners become short curves.
  const n = outline.length;
  outline.forEach(([x, y], i) => {
    const [px, py] = outline[(i + n - 1) % n]!;
    const [nx, ny] = outline[(i + 1) % n]!;
    const cut = (tx: number, ty: number): [number, number] => {
      const len = Math.hypot(tx - x, ty - y);
      const k = Math.min(0.16, len * 0.45) / len;
      return [x + (tx - x) * k, y + (ty - y) * k];
    };
    const [a, b] = [cut(px, py), cut(nx, ny)];
    if (i === 0) shape.moveTo(...a);
    else shape.lineTo(...a);
    shape.quadraticCurveTo(x, y, ...b);
  });
  const g = new ExtrudeGeometry(shape, {
    depth: width - 2 * round,
    bevelEnabled: true,
    bevelThickness: round,
    bevelSize: round,
    bevelOffset: -round,
    bevelSegments: 3,
    curveSegments: 4,
  });
  return g.translate(0, 0, -(width - 2 * round) / 2).rotateY(Math.PI / 2);
}

interface CarShape {
  length: number;
  body: [number, number][];
  cabin: [number, number][];
  windows: [number, number][][];
  /** The lower and the upper end of the windscreen and of the rear window. */
  front: [[number, number], [number, number]];
  rear: [[number, number], [number, number]];
}

/** x is along the car, the front at the right, which slab() turns to negative z. */
const CAR_SHAPES: CarShape[] = [
  {
    // A small hatchback.
    length: 3.9,
    body: [[-1.92, 0.27], [-1.97, 0.7], [-1.86, 0.98], [0.8, 0.97], [1.62, 0.85], [1.95, 0.62], [1.93, 0.27]],
    cabin: [[-1.86, 0.86], [-1.5, 1.46], [-0.9, 1.52], [-0.1, 1.5], [0.3, 1.4], [1.1, 0.86]],
    windows: [
      [[-1.38, 1.0], [-1.18, 1.36], [-0.42, 1.4], [-0.42, 1.0]],
      [[-0.34, 1.0], [-0.34, 1.4], [0.12, 1.36], [0.7, 1.0]],
    ],
    front: [[1.02, 0.95], [0.36, 1.38]],
    rear: [[-1.8, 1.0], [-1.56, 1.4]],
  },
  {
    // An estate car.
    length: 4.6,
    body: [[-2.24, 0.29], [-2.28, 0.74], [-2.18, 1.0], [0.75, 0.97], [1.9, 0.84], [2.27, 0.62], [2.25, 0.29]],
    cabin: [[-2.2, 0.88], [-1.9, 1.4], [-1.5, 1.48], [-0.2, 1.48], [0.2, 1.38], [1.05, 0.86]],
    windows: [
      [[-1.86, 1.0], [-1.68, 1.33], [-1.2, 1.37], [-1.2, 1.0]],
      [[-1.12, 1.0], [-1.12, 1.37], [-0.36, 1.38], [-0.36, 1.0]],
      [[-0.28, 1.0], [-0.28, 1.38], [0.06, 1.34], [0.64, 1.0]],
    ],
    front: [[0.97, 0.95], [0.26, 1.36]],
    rear: [[-2.16, 1.02], [-1.95, 1.36]],
  },
  {
    // A tall family car.
    length: 4.2,
    body: [[-2.06, 0.31], [-2.1, 0.85], [-2.0, 1.1], [0.9, 1.06], [1.75, 0.96], [2.08, 0.72], [2.06, 0.31]],
    cabin: [[-2.02, 0.98], [-1.82, 1.58], [-1.45, 1.68], [-0.1, 1.67], [0.36, 1.54], [1.2, 0.96]],
    windows: [
      [[-1.64, 1.12], [-1.5, 1.5], [-0.5, 1.55], [-0.5, 1.12]],
      [[-0.42, 1.12], [-0.42, 1.55], [0.18, 1.5], [0.76, 1.12]],
    ],
    front: [[1.12, 1.05], [0.42, 1.52]],
    rear: [[-2.0, 1.14], [-1.86, 1.54]],
  },
];

const WIDE = 1.76;

/** Cars are narrower at the top. */
function taper(g: BufferGeometry, from: number): BufferGeometry {
  const p = g.getAttribute("position");
  for (let i = 0; i < p.count; i += 1) {
    const over = Math.max(p.getY(i) - from, 0);
    p.setX(i, p.getX(i) * (1 - 0.3 * over));
  }
  g.computeVertexNormals();
  return g;
}

/** A window across the car, between two points of its side. */
function pane([[x0, y0], [x1, y1]]: [[number, number], [number, number]], width: number, out: number): BufferGeometry {
  const [dx, dy] = [x1 - x0, y1 - y0];
  const length = Math.hypot(dx, dy);
  const front = dx < 0 ? 1 : -1; // it leans away from its own end of the car
  return new BoxGeometry(width, 0.012, length)
    .rotateX(Math.atan2(-dy, -dx))
    .translate(0, (y0 + y1) / 2 + (Math.abs(dx) / length) * out, -((x0 + x1) / 2 + front * (dy / length) * out));
}

function flatSide(outline: [number, number][], width: number): BufferGeometry {
  const shape = new Shape();
  outline.forEach(([x, y], i) => (i === 0 ? shape.moveTo(x, y) : shape.lineTo(x, y)));
  return new ExtrudeGeometry(shape, { depth: width, bevelEnabled: false }).translate(0, 0, -width / 2).rotateY(Math.PI / 2);
}

export interface CarParts {
  /** Takes the color of the car. */
  paint: BufferGeometry;
  rest: BufferGeometry;
}

const GLASS = "#10161b";

export function carParts(): CarParts[] {
  return CAR_SHAPES.map(({ length, body, cabin, windows, front, rear }) => {
    const half = length / 2;
    const belt = cabin[0]![1];
    const tall = Math.max(...cabin.map(([, y]) => y)) > 1.6 ? 0.1 : 0;
    const paint = merge([
      part(taper(slab(body, WIDE, 0.14), 0.62), "#ffffff"),
      part(taper(slab(cabin, WIDE - 0.16, 0.12), belt), "#ffffff"),
      part(box(0.16, 0.1, 0.13), "#ffffff", { at: [-WIDE / 2 - 0.04, 0.99 + tall, -0.62] }),
      part(box(0.16, 0.1, 0.13), "#ffffff", { at: [WIDE / 2 + 0.04, 0.99 + tall, -0.62] }),
    ]);
    const parts = [
      ...windows.map((w) => part(taper(flatSide(w, WIDE - 0.135), belt), GLASS)),
      part(taper(pane(front, WIDE - 0.32, 0.028), belt), GLASS),
      part(taper(pane(rear, WIDE - 0.36, 0.028), belt), GLASS),
      part(box(WIDE - 0.02, 0.1, length * 0.48), "#17191b", { at: [0, 0.27, 0] }),
    ];
    const axle = half - 0.76;
    for (const x of [-1, 1]) {
      for (const z of [-axle, axle]) {
        const side: [number, number, number] = [0, 0, Math.PI / 2];
        parts.push(
          part(tube(0.385, 0.06, 0.385, 26), "#0b0c0d", { turn: side, at: [x * (WIDE / 2 - 0.015), 0.36, z] }),
          part(tube(0.32, 0.2, 0.32, 24), "#141518", { turn: side, at: [x * (WIDE / 2 - 0.07), 0.32, z] }),
          part(tube(0.215, 0.02, 0.215, 20), "#8e9499", { turn: side, at: [x * (WIDE / 2 + 0.03), 0.32, z] }),
          part(tube(0.07, 0.03, 0.07, 12), "#3a3d40", { turn: side, at: [x * (WIDE / 2 + 0.035), 0.32, z] }),
        );
      }
      parts.push(
        part(box(0.26, 0.17, 0.04), "#a50f16", { at: [x * (WIDE / 2 - 0.31), 0.84 + tall, half - 0.04] }),
        part(box(0.26, 0.045, 0.042), "#d9682f", { at: [x * (WIDE / 2 - 0.31), 0.775 + tall, half - 0.04] }),
        part(box(0.34, 0.11, 0.06), "#eef0ee", { at: [x * (WIDE / 2 - 0.36), 0.69 + tall, -half + 0.02] }),
      );
      for (const z of [0.5, -0.4]) parts.push(part(box(0.02, 0.028, 0.18), "#202428", { at: [x * (WIDE / 2 - 0.035), 0.86 + tall, z] }));
    }
    parts.push(
      part(box(0.52, 0.12, 0.03), "#f4f4f1", { at: [0, 0.64 + tall, half + 0.004] }),
      part(box(0.4, 0.065, 0.034), "#33373b", { at: [0.02, 0.64 + tall, half + 0.012] }),
      part(box(0.52, 0.12, 0.03), "#f4f4f1", { at: [0, 0.5, -half - 0.012] }),
      part(box(1.4, 0.2, 0.06), "#1a1c1e", { at: [0, 0.36, half - 0.01] }),
      part(box(1.3, 0.13, 0.06), "#1a1c1e", { at: [0, 0.35, -half + 0.01] }),
    );
    return { paint, rest: merge(parts) };
  });
}

/** The trunk stands at the origin. Its leaves are cards with normals that point out, so they shade as one ball. */
export function tree(): { trunk: BufferGeometry; leaves: BufferGeometry } {
  const trunk = merge([
    part(tube(0.13, 2.9, 0.09, 8), "#57463a", { at: [0, 1.45, 0] }),
    rod([0, 2.4, 0], [0.7, 3.6, 0.2], 0.05, "#57463a"),
    rod([0, 2.5, 0], [-0.6, 3.7, -0.3], 0.05, "#57463a"),
    rod([0, 2.6, 0], [0.1, 4.2, 0.4], 0.05, "#57463a"),
    part(box(1.1, 0.03, 1.1), "#3b3733", { at: [0, 0.015, 0] }),
  ]);
  return { trunk, leaves: leafBall(64, [1.5, 1.45, 1.5], 4.05, 1.2, 5) };
}

function leafBall(count: number, radius: [number, number, number], y: number, size: number, seed: number): BufferGeometry {
  const rnd = random(seed);
  const cards: BufferGeometry[] = [];
  for (let i = 0; i < count; i += 1) {
    const up = 1 - (2 * (i + 0.5)) / count;
    const a = i * 2.39996;
    const out = new Vector3(Math.cos(a) * Math.sqrt(1 - up * up), up, Math.sin(a) * Math.sqrt(1 - up * up));
    const depth = 0.52 + rnd() * 0.48;
    const s = size * (0.75 + rnd() * 0.5);
    const g = new PlaneGeometry(s, s);
    g.rotateZ(rnd() * 6.28);
    g.lookAt(out.clone().add(new Vector3(rnd() - 0.5, rnd() - 0.5, rnd() - 0.5)));
    g.translate(out.x * radius[0] * depth, y + out.y * radius[1] * depth, out.z * radius[2] * depth);
    const normal = g.getAttribute("normal");
    for (let v = 0; v < normal.count; v += 1) normal.setXYZ(v, out.x, out.y * 0.9 + 0.25, out.z);
    cards.push(g);
  }
  return merge(cards);
}

export function planter(): { pot: BufferGeometry; leaves: BufferGeometry } {
  const pot = merge([
    part(tube(0.26, 0.55, 0.34, 4), "#2f3438", { turn: [0, Math.PI / 4, 0], at: [0, 0.28, 0] }),
    part(box(0.44, 0.04, 0.44), "#2a1f18", { at: [0, 0.54, 0] }),
  ]);
  return { pot, leaves: leafBall(16, [0.36, 0.42, 0.36], 0.95, 0.5, 9) };
}

/** A lantern on an iron post. */
export function lamp(): { post: BufferGeometry; light: BufferGeometry } {
  const iron = "#1f2724";
  const post = merge([
    part(tube(0.12, 0.5, 0.09), iron, { at: [0, 0.25, 0] }),
    part(tube(0.055, 3.3, 0.04), iron, { at: [0, 2.0, 0] }),
    part(tube(0.07, 0.08), iron, { at: [0, 1.1, 0] }),
    part(tube(0.08, 0.1), iron, { at: [0, 3.6, 0] }),
    part(tube(0.25, 0.18, 0.04, 6), iron, { at: [0, 4.18, 0] }),
    part(tube(0.03, 0.14, 0.03, 6), iron, { at: [0, 4.32, 0] }),
    ...[0, 1, 2, 3, 4, 5].map((i) => {
      const a = (i * Math.PI) / 3;
      return rod([Math.sin(a) * 0.14, 3.66, Math.cos(a) * 0.14], [Math.sin(a) * 0.22, 4.1, Math.cos(a) * 0.22], 0.012, iron);
    }),
  ]);
  return { post, light: part(tube(0.13, 0.42, 0.2, 6), "#fff2d4", { at: [0, 3.88, 0] }) };
}

export function bike(): BufferGeometry {
  const frame = "#26323a";
  const wheel = (z: number) => part(new TorusGeometry(0.34, 0.018, 6, 26), "#16181a", { turn: [0, Math.PI / 2, 0], at: [0, 0.35, z] });
  const spokes = (z: number) => [0, 1, 2].map((i) => {
    const a = (i * Math.PI) / 3;
    return rod([0, 0.35 + Math.sin(a) * 0.33, z + Math.cos(a) * 0.33], [0, 0.35 - Math.sin(a) * 0.33, z - Math.cos(a) * 0.33], 0.004, "#8d9296");
  });
  return merge([
    wheel(-0.52), wheel(0.52), ...spokes(-0.52), ...spokes(0.52),
    rod([0, 0.35, 0.52], [0, 0.4, 0.06], 0.014, frame),
    rod([0, 0.4, 0.06], [0, 0.86, 0.2], 0.016, frame),
    rod([0, 0.35, 0.52], [0, 0.8, 0.19], 0.012, frame),
    rod([0, 0.4, 0.06], [0, 0.82, -0.34], 0.018, frame),
    rod([0, 0.78, 0.18], [0, 0.84, -0.34], 0.016, frame),
    rod([0, 0.35, -0.52], [0, 0.98, -0.34], 0.016, frame),
    rod([-0.26, 1.0, -0.28], [0.26, 1.0, -0.28], 0.014, "#1b1d1f"),
    part(box(0.14, 0.06, 0.26), "#6a4126", { at: [0, 0.92, 0.22] }),
    part(box(0.34, 0.22, 0.28), "#8c6a43", { at: [0, 0.86, -0.62] }),
  ]);
}

/** One meter wide, high and deep each way, and stretched to the house. The front is at z = 0. */
export function roof(): BufferGeometry {
  const shape = new Shape();
  shape.moveTo(0.35, 0);
  shape.lineTo(-0.9, 1.5);
  shape.lineTo(-5, 3.4);
  shape.lineTo(-9, 1.5);
  shape.lineTo(-10, 0);
  const g = new ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false });
  // x of the outline is depth, so it goes to z, and the extrusion goes along x.
  g.rotateY(-Math.PI / 2).translate(0.5, 0, 0);
  return merge([part(g, "#ffffff")]);
}

/** Two dormers and two chimneys, for a house that is one wide. */
export function dormer(): { walls: BufferGeometry; roof: BufferGeometry } {
  return {
    walls: merge([
      part(box(1.3, 1.5, 1.7), "#e6dccb", { at: [0, 0.85, -1.1] }),
      part(box(0.8, 1.0, 0.04), "#2b3440", { at: [0, 0.85, -0.23] }),
      part(box(0.05, 1.0, 0.06), "#f1ece0", { at: [0, 0.85, -0.22] }),
      part(box(0.8, 0.05, 0.06), "#f1ece0", { at: [0, 0.95, -0.22] }),
    ]),
    roof: merge([part(tube(1.0, 1.9, 1.0, 3), "#ffffff", { turn: [Math.PI / 2, 0, 0], at: [0, 1.6, -1.1] })]),
  };
}

export function chimney(): BufferGeometry {
  return merge([
    part(box(0.9, 2.0, 0.7), "#9a5a43", { at: [0, 1.0, 0] }),
    part(box(1.0, 0.16, 0.8), "#55504c", { at: [0, 2.0, 0] }),
  ]);
}

/** The bonnet of the Spark, as far as it is seen over the dashboard. Two soft ridges run along it. */
export function bonnet(): BufferGeometry {
  const g = new SphereGeometry(1, 120, 40, 0, Math.PI * 2, 0, Math.PI / 2);
  g.scale(1.05, 0.16, 1.5);
  const p = g.getAttribute("position");
  for (let i = 0; i < p.count; i += 1) {
    const x = Math.abs(p.getX(i));
    const ridge = Math.exp(-(((x - 0.52) / 0.13) ** 2)) * 0.02 - Math.exp(-(((x - 0.8) / 0.1) ** 2)) * 0.012;
    p.setY(i, p.getY(i) + ridge);
  }
  g.computeVertexNormals();
  return g;
}
