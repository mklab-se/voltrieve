/** Draws the street around the car. It reads the simulation and never changes it. */
import {
  Box3,
  Color,
  CylinderGeometry,
  DirectionalLight,
  DoubleSide,
  Fog,
  Group,
  HalfFloatType,
  HemisphereLight,
  BoxGeometry,
  InstancedMesh,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshPhysicalMaterial,
  MeshStandardMaterial,
  NoToneMapping,
  Object3D,
  PCFShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  PMREMGenerator,
  Scene,
  Vector3,
  WebGLRenderer,
  WebGLRenderTarget,
  type BufferGeometry,
  type Material,
} from "three";
import { MIRROR, PICTURE, WINDSCREEN } from "../layout";
import type { Car } from "../sim/car";
import { signDef, type SignDef } from "../sim/signs";
import { BAYS_AT_M, BLOCK_M, blockOf, SIGN_AT_M, signOf, type SignId } from "../sim/street";
import type { WiperMode } from "../tools";
import { Glass } from "./glass";
import { bike, bonnet, carParts, chimney, dormer, lamp, planter, roof, tree } from "./models";
import { FACADE_LEFT, FACADE_RIGHT, FACADES, KERB_LEFT, KERB_RIGHT, planSegment, SEGMENT_M, SIGN_X, STRIP_RIGHT, type Facade, type House } from "./plan";
import { blurPx, motionBlur, PHOTO_PX, signCamera, toJpeg, type Board } from "./signCamera";
import { HORIZON, Rain, Sky } from "./sky";
import { makeTextures, PAVEMENT_LENGTH_M, PAVEMENT_WIDTHS_M, ROAD_LENGTH_M, ROAD_WIDTH_M, type Textures } from "./textures";
import { PARKED, stepWipers, type Wipers } from "./wipers";

export { blurPx } from "./signCamera";

export type Renderer = Pick<WebGLRenderer, "render" | "setSize" | "setPixelRatio" | "setRenderTarget" | "dispose"> & {
  shadowMap: { needsUpdate: boolean };
};

const SEGMENTS_BEHIND = 2;
const SEGMENTS = 10;
const BEHIND_M = SEGMENTS_BEHIND * SEGMENT_M;
const GROUND_M = 368;
/** The origin moves this far at a time: numbers far from zero are too coarse for the graphics card. */
const REBASE_M = 2000;

/** Far enough for the sky. */
const FAR_M = 1200;
const EYE_M = 1.36;
/** The horizon in the cockpit picture, in pixels from its top, and how long its lens is. */
const HORIZON_PX = 306;
const FOCAL_PX = 1010;
const BONNET = new Color("#f2b61c");
/** How far above the road a row of a sign is tall. */
const SIGN_ROW_M = 0.155;
const SIGN_LOW_M = 1.72;
const SIGN_TURN = -0.1;

const SUN = new Vector3(-0.3, 0.6, 0.86).normalize();
const SUN_LIGHT = 4.6;
const SKY_LIGHT = 1.7;
const SKY_COLOR = { dry: new Color("#cfe2f7"), rain: new Color("#cbd6de") };
const GROUND_COLOR = { dry: new Color("#b79b78"), rain: new Color("#83898c") };

const PAINT = ["#e9e8e4", "#b9bec2", "#3b4046", "#22324a", "#2f4a3f", "#7c2b25", "#8fa9b8", "#17181b"].map((c) => new Color(c));
const ROOFS = ["#3c3a3a", "#6b3b2d", "#4d6a60", "#57524f"].map((c) => new Color(c));
const WALLS: Record<Facade, Color> = {
  ochre: new Color("#d9a23c"),
  red: new Color("#9c3526"),
  cream: new Color("#e6cf9d"),
  cafe: new Color("#cf6f26"),
  salmon: new Color("#d98f78"),
};
const WHITE = new Color("#ffffff");

const MAX_PIXELS = 3_400_000;
/** The share of the full resolution, from the best picture to the fastest. */
const QUALITY = [1, 0.8, 0.62, 0.5];
const SLOW_FRAME_S = 1 / 45;
const JUDGE_FRAMES = 90;

/** Many copies of one thing. Each segment of the street has its own places for them. */
class Copies {
  readonly meshes: InstancedMesh[];
  private next = 0;
  private end = 0;

  constructor(
    parent: Object3D,
    private readonly perSegment: number,
    parts: { geometry: BufferGeometry; material: Material; shadow?: boolean; receive?: boolean; colored?: boolean }[],
  ) {
    this.meshes = parts.map(({ geometry, material, shadow = true, receive = true, colored = false }) => {
      const mesh = new InstancedMesh(geometry, material, perSegment * SEGMENTS);
      mesh.castShadow = shadow;
      mesh.receiveShadow = receive;
      mesh.frustumCulled = false;
      mesh.userData.colored = colored;
      const none = new Matrix4().makeScale(0, 0, 0);
      for (let i = 0; i < mesh.count; i += 1) {
        mesh.setMatrixAt(i, none);
        if (colored) mesh.setColorAt(i, WHITE);
      }
      parent.add(mesh);
      return mesh;
    });
  }

  begin(slot: number): void {
    this.next = slot * this.perSegment;
    this.end = this.next + this.perSegment;
  }

  add(matrix: Matrix4, color?: Color): void {
    if (this.next === this.end) return;
    for (const mesh of this.meshes) {
      mesh.setMatrixAt(this.next, matrix);
      if (color && mesh.userData.colored) mesh.setColorAt(this.next, color);
    }
    this.next += 1;
  }

  finish(): void {
    const none = new Matrix4().makeScale(0, 0, 0);
    while (this.next < this.end) {
      for (const mesh of this.meshes) mesh.setMatrixAt(this.next, none);
      this.next += 1;
    }
    for (const mesh of this.meshes) {
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  }
}

interface SignPost {
  group: Group;
  board: Mesh<PlaneGeometry, MeshStandardMaterial>;
  back: Mesh<PlaneGeometry, MeshStandardMaterial>;
  post: Mesh;
  block: number;
  size: { width: number; height: number };
}

const smoothstep = (t: number) => t * t * (3 - 2 * t);
const clamp = (n: number, low: number, high: number) => Math.min(Math.max(n, low), high);

export interface Parts {
  renderer?: Renderer;
  textures?: Textures;
}

export class StreetScene {
  readonly scene = new Scene();
  private readonly renderer: Renderer;
  private readonly textures: Textures;
  private readonly glass = new Glass();
  private readonly camera = new PerspectiveCamera();
  private readonly mirrorCamera = new PerspectiveCamera(17, MIRROR.width * PICTURE.width / (MIRROR.height * PICTURE.height), 0.5, FAR_M);
  private readonly target = new WebGLRenderTarget(4, 4, { type: HalfFloatType, samples: 4 });
  private readonly mirror = new WebGLRenderTarget(4, 4, { type: HalfFloatType, samples: 4 });
  private readonly sky = new Sky(SUN);
  private readonly rain = new Rain();
  private readonly sun = new DirectionalLight("#ffd9a6", SUN_LIGHT);
  private readonly hemisphere = new HemisphereLight(SKY_COLOR.dry, GROUND_COLOR.dry, SKY_LIGHT);
  private readonly fog = new Fog(HORIZON.dry.clone(), 70, 300);
  private readonly rig = new Group();
  private readonly hood: Mesh<BufferGeometry, MeshPhysicalMaterial>;
  private readonly skyline: Mesh<PlaneGeometry, MeshBasicMaterial>;
  private readonly ground: { mesh: Object3D; every: number }[] = [];
  private readonly wetStones: MeshStandardMaterial[] = [];
  private readonly kinds: ReturnType<StreetScene["makeKinds"]>;
  private readonly signs: SignPost[];
  private readonly signMaterials = new Map<SignId, { front: MeshStandardMaterial; back: MeshStandardMaterial; def: SignDef }>();
  private readonly slots: (number | undefined)[] = new Array(SEGMENTS).fill(undefined);
  private base = 0;
  private shadowAt?: number;
  private readonly still: boolean;

  private wipers: Wipers = PARKED;
  private wet = 0;
  private timeS = 0;
  private before?: number;
  private last?: Car;
  private yaw = 0;
  private pitch = 0;
  private sway = 0;
  private level = 0;
  private judged = { frames: 0, slow: 0 };
  private size = { width: 0, height: 0, dpr: 0, level: -1 };

  constructor(
    private readonly canvas: HTMLCanvasElement,
    parts: Parts = {},
  ) {
    this.still = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    this.textures = parts.textures ?? makeTextures();
    this.renderer = parts.renderer ?? makeRenderer(canvas);

    this.scene.fog = this.fog;
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0004;
    this.sun.shadow.normalBias = 0.03;
    this.sun.shadow.radius = 2.2;
    this.sun.shadow.camera.up.set(0, 0, -1); // so that the street runs straight through the shadow map
    this.scene.add(this.sky, this.hemisphere, this.sun, this.sun.target, this.rig);

    this.makeGround();
    this.kinds = this.makeKinds();
    this.signs = [this.makeSign(), this.makeSign()];
    this.skyline = new Mesh(
      new PlaneGeometry(168, 84),
      new MeshBasicMaterial({ map: this.textures.skyline, transparent: true, fog: false, depthWrite: false }),
    );
    this.skyline.renderOrder = -1;
    this.scene.add(this.skyline);

    this.hood = new Mesh(
      bonnet(),
      new MeshPhysicalMaterial({ color: BONNET, roughness: 0.42, clearcoat: 1, clearcoatRoughness: 0.12, envMapIntensity: 1.6 }),
    );
    this.hood.position.set(0, -0.422, -1.35);
    this.rig.add(this.camera, this.hood, this.rain);
    this.camera.near = 0.3;
    this.camera.far = FAR_M;
    this.mirrorCamera.rotation.y = Math.PI;
    this.mirrorCamera.position.set(0.1, 0.12, 0);
    this.rig.add(this.mirrorCamera);

    if (this.renderer instanceof WebGLRenderer) this.lightFromSky(this.renderer);
  }

  /** Draw one frame. */
  render(car: Car, wipers: WiperMode): void {
    const now = performance.now();
    const dt = this.before === undefined ? 0 : Math.min((now - this.before) / 1000, 0.1);
    if (this.before !== undefined) this.judge((now - this.before) / 1000);
    this.before = now;
    if (!this.resize()) return;
    this.timeS += dt;
    this.wipers = stepWipers(this.wipers, wipers, dt);
    this.wet = clamp(this.wet + (wipers === "off" ? -dt / 3 : dt / 2.5), 0, 1);
    this.weather();
    this.follow(car, dt);
    this.rain.step(dt, car.speedKmh / 3.6, this.wet);

    const showMirror = this.level < 2;
    if (showMirror) {
      this.hood.visible = this.rain.visible = false;
      this.renderer.setRenderTarget(this.mirror);
      this.renderer.render(this.scene, this.mirrorCamera);
      this.hood.visible = true;
      this.rain.visible = this.wet > 0.01;
    }
    this.renderer.setRenderTarget(this.target);
    this.renderer.render(this.scene, this.camera);
    this.renderer.setRenderTarget(null);
    this.glass.windscreen(this.target.texture, showMirror ? this.mirror.texture : undefined, {
      wipers: this.wipers,
      timeS: this.timeS,
      rain: this.wet,
      speed: clamp(car.speedKmh / 130, 0, 1),
      pixel: (PICTURE.width * WINDSCREEN.width) / 100 / this.target.width,
    });
    this.renderer.render(this.glass.scene, this.glass.camera);
  }

  /** A close-up of the sign of a block, as the car sees it on its way past. */
  async photo(car: Car, block: number): Promise<Blob> {
    if (!(this.renderer instanceof WebGLRenderer)) throw new Error("The photo could not be taken.");
    const renderer = this.renderer;
    this.move(car.distanceM);
    const sign = this.signs.find((s) => s.block === block) ?? this.setSign(this.signs[0]!, block);
    const raw = new WebGLRenderTarget(PHOTO_PX, PHOTO_PX, { type: HalfFloatType, samples: 4 });
    const developed = new WebGLRenderTarget(PHOTO_PX, PHOTO_PX, { depthBuffer: false });
    try {
      const camera = signCamera(this.boardOf(sign));
      this.scene.add(camera);
      this.hood.visible = this.rain.visible = false;
      renderer.setRenderTarget(raw);
      renderer.render(this.scene, camera);
      this.hood.visible = true;
      this.scene.remove(camera);
      this.glass.plain(raw.texture);
      renderer.setRenderTarget(developed);
      renderer.render(this.glass.scene, this.glass.camera);
      renderer.setRenderTarget(null);
      const pixels = new Uint8Array(4 * PHOTO_PX * PHOTO_PX);
      await renderer.readRenderTargetPixelsAsync(developed, 0, 0, PHOTO_PX, PHOTO_PX, pixels);
      return await toJpeg(motionBlur(upright(pixels), blurPx(car.speedKmh)));
    } finally {
      raw.dispose();
      developed.dispose();
    }
  }

  /** The blocks whose signs stand on the street now. */
  signBlocks(): number[] {
    return this.signs.map((s) => s.block).sort((a, b) => a - b);
  }

  dispose(): void {
    const seen = new Set<{ dispose(): void }>();
    this.scene.traverse((o) => {
      if (!(o instanceof Mesh)) return;
      seen.add(o.geometry);
      for (const m of [o.material].flat() as Material[]) {
        seen.add(m);
        for (const value of Object.values(m)) if (value && typeof value === "object" && "isTexture" in value) seen.add(value as { dispose(): void });
      }
    });
    for (const thing of seen) thing.dispose();
    this.sun.dispose();
    this.target.dispose();
    this.mirror.dispose();
    this.glass.dispose();
    this.renderer.dispose();
  }

  // ---- the frame ----

  private resize(): boolean {
    const { clientWidth: width, clientHeight: height } = this.canvas;
    if (width === 0 || height === 0) return false;
    const dpr = globalThis.devicePixelRatio || 1;
    const { size } = this;
    if (size.width === width && size.height === height && size.dpr === dpr && size.level === this.level) return true;
    this.size = { width, height, dpr, level: this.level };
    const ratio = Math.min(dpr, 2, Math.sqrt(MAX_PIXELS / (width * height))) * QUALITY[this.level]!;
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(width, height, false);
    this.target.setSize(Math.round(width * ratio), Math.round(height * ratio));
    const share = { x: MIRROR.width / WINDSCREEN.width, y: MIRROR.height / WINDSCREEN.height };
    this.mirror.setSize(Math.max(1, Math.round(width * ratio * share.x)), Math.max(1, Math.round(height * ratio * share.y)));

    // The lens of the whole cockpit picture, of which the windscreen is a part. The horizon is not in
    // the middle of it, and the walls still have to be upright.
    const full = { w: PICTURE.width, h: 2 * HORIZON_PX };
    this.camera.fov = (2 * Math.atan(HORIZON_PX / FOCAL_PX) * 180) / Math.PI;
    this.camera.aspect = full.w / full.h;
    const px = (percent: number, of: number) => (percent / 100) * of;
    this.camera.setViewOffset(
      full.w,
      full.h,
      px(WINDSCREEN.left, PICTURE.width),
      px(WINDSCREEN.top, PICTURE.height),
      px(WINDSCREEN.width, PICTURE.width),
      px(WINDSCREEN.height, PICTURE.height),
    );
    return true;
  }

  /** When most frames are slow, the picture gets smaller, and then the mirror goes. A single hitch does not count. */
  private judge(seconds: number): void {
    const { judged } = this;
    judged.frames += 1;
    if (seconds > SLOW_FRAME_S && seconds < 0.5) judged.slow += 1;
    if (judged.frames < JUDGE_FRAMES) return;
    if (judged.slow > 0.6 * JUDGE_FRAMES && this.level < QUALITY.length - 1) this.level += 1;
    this.judged = { frames: 0, slow: 0 };
  }

  private weather(): void {
    const { wet } = this;
    this.sky.rain = wet;
    this.fog.color.lerpColors(HORIZON.dry, HORIZON.rain, wet);
    this.fog.near = 70 - 60 * wet;
    this.fog.far = 300 - 95 * wet;
    this.sun.intensity = SUN_LIGHT * (1 - 0.86 * wet);
    this.hemisphere.intensity = SKY_LIGHT * (1 - 0.28 * wet);
    this.hemisphere.color.lerpColors(SKY_COLOR.dry, SKY_COLOR.rain, wet);
    this.hemisphere.groundColor.lerpColors(GROUND_COLOR.dry, GROUND_COLOR.rain, wet);
    this.skyline.material.opacity = 1 - 0.75 * wet;
    this.glass.exposure = 1.12 - 0.1 * wet;
    for (const m of this.wetStones) {
      m.roughness = 0.92 - 0.7 * wet;
      m.color.setRGB(1 - 0.48 * wet, 1 - 0.42 * wet, 1 - 0.34 * wet);
      m.envMapIntensity = 0.5 + 1.7 * wet;
    }
  }

  private follow(car: Car, dt: number): void {
    this.move(car.distanceM);
    const x = smoothstep(clamp(car.lateral, 0, 1)) * STRIP_RIGHT;
    const { last } = this;
    if (last && dt > 0) {
      const ahead = car.distanceM - last.distanceM;
      const aside = x - smoothstep(clamp(last.lateral, 0, 1)) * STRIP_RIGHT;
      const heading = ahead > 0.001 ? clamp(Math.atan2(aside, ahead), -0.3, 0.3) : 0;
      this.yaw += (heading - this.yaw) * Math.min(dt * 4, 1);
      const pull = clamp((car.speedKmh - last.speedKmh) / dt / 25, -1, 1);
      this.pitch += (pull * 0.011 - this.pitch) * Math.min(dt * 5, 1);
      this.sway += dt * (0.6 + car.speedKmh / 40);
    }
    this.last = car;
    const pace = this.still ? 0 : Math.min(car.speedKmh / 100, 1.3);
    const s = this.sway;
    this.rig.position.set(
      x + Math.sin(s * 0.7) * 0.012 * pace,
      EYE_M + (Math.sin(s * 5.1) * 0.004 + Math.sin(s * 8.3) * 0.0025) * pace,
      -(car.distanceM - this.base),
    );
    this.rig.rotation.set(0, -this.yaw * 0.85 - Math.sin(s * 0.43) * 0.002 * pace, 0);
    const pitch = this.still ? 0 : this.pitch;
    this.camera.rotation.set(pitch, 0, -this.yaw * 0.04 + Math.sin(s * 0.9) * 0.0012 * pace);
  }

  /** Put everything where a car at this place needs it. */
  private move(distanceM: number): void {
    const base = Math.floor(distanceM / REBASE_M) * REBASE_M;
    if (base !== this.base) {
      this.base = base;
      this.slots.fill(undefined);
      this.shadowAt = undefined;
      for (const sign of this.signs) sign.block = -1;
    }
    const here = Math.floor(distanceM / SEGMENT_M);
    for (let s = here - SEGMENTS_BEHIND; s < here - SEGMENTS_BEHIND + SEGMENTS; s += 1) {
      const slot = ((s % SEGMENTS) + SEGMENTS) % SEGMENTS;
      if (this.slots[slot] === s) continue;
      this.slots[slot] = s;
      this.fill(slot, s);
    }
    const z = -(distanceM - this.base);
    for (const { mesh, every } of this.ground) mesh.position.z = -Math.floor((distanceM - this.base) / every) * every + BEHIND_M - GROUND_M / 2;
    this.sky.position.set(0, 0, z);
    this.skyline.position.set(-1.45, 33.4, z - 430);
    const first = Math.max(Math.ceil((distanceM - BEHIND_M - SIGN_AT_M) / BLOCK_M), 0);
    for (const block of [first, first + 1]) {
      const sign = this.signs[block % 2]!;
      if (sign.block !== block) this.setSign(sign, block);
    }
    this.castShadows(z);
  }

  /** The shadows stand still, so they are drawn again only when the car has come a few meters. */
  private castShadows(z: number): void {
    const { camera } = this.sun.shadow;
    const right = new Vector3(0, 0, -1).cross(SUN).normalize();
    const up = SUN.clone().cross(right);
    if (this.shadowAt === undefined) {
      const box = new Box3(new Vector3(FACADE_LEFT, 0, -150), new Vector3(FACADE_RIGHT, 13, 24));
      const [u, v, w]: number[][] = [[], [], []];
      for (const x of [box.min.x, box.max.x]) {
        for (const y of [box.min.y, box.max.y]) {
          for (const dz of [box.min.z, box.max.z]) {
            const p = new Vector3(x, y, dz);
            u!.push(p.dot(right));
            v!.push(p.dot(up));
            w!.push(p.dot(SUN));
          }
        }
      }
      camera.left = Math.min(...u!);
      camera.right = Math.max(...u!);
      camera.bottom = Math.min(...v!);
      camera.top = Math.max(...v!);
      camera.near = 1;
      camera.far = Math.max(...w!) - Math.min(...w!) + 120;
      this.sun.userData.away = Math.max(...w!) + 60;
      camera.updateProjectionMatrix();
    }
    // A whole number of shadow pixels at a time: else the edges would crawl.
    const pixel = (camera.top - camera.bottom) / this.sun.shadow.mapSize.y / Math.abs(up.z);
    const step = Math.max(1, Math.round(5 / pixel)) * pixel;
    const at = Math.round(z / step) * step;
    if (at === this.shadowAt) return;
    this.shadowAt = at;
    this.sun.target.position.set(0, 0, at);
    this.sun.position.set(0, 0, at).addScaledVector(SUN, this.sun.userData.away as number);
    this.sun.target.updateMatrixWorld();
    this.renderer.shadowMap.needsUpdate = true;
  }

  // ---- the street ----

  private makeGround(): void {
    const { road, pavement } = this.textures;
    const flat = (width: number, x: number, y: number, map: typeof road, every: number) => {
      const [color, bump] = [map.map.clone(), map.bump.clone()];
      color.repeat.set(1, GROUND_M / every);
      bump.repeat.copy(color.repeat);
      const material = new MeshStandardMaterial({ map: color, bumpMap: bump, bumpScale: 2.2, roughness: 0.92 });
      this.wetStones.push(material);
      const mesh = new Mesh(new PlaneGeometry(width, GROUND_M).rotateX(-Math.PI / 2), material);
      mesh.position.set(x, y, 0);
      mesh.receiveShadow = true;
      this.add(mesh, every);
    };
    flat(ROAD_WIDTH_M, (KERB_LEFT + KERB_RIGHT) / 2, 0, road, ROAD_LENGTH_M);
    flat(PAVEMENT_WIDTHS_M.left, (KERB_LEFT + FACADE_LEFT) / 2, 0.13, pavement, PAVEMENT_LENGTH_M);
    flat(PAVEMENT_WIDTHS_M.right, (KERB_RIGHT + FACADE_RIGHT) / 2, 0.13, pavement, PAVEMENT_LENGTH_M);
    const stone = new MeshStandardMaterial({ color: "#a59b90", roughness: 0.8 });
    for (const x of [KERB_LEFT, KERB_RIGHT]) {
      const kerb = new Mesh(new BoxGeometry(0.24, 0.15, GROUND_M), stone);
      kerb.position.set(x, 0.07, 0);
      kerb.receiveShadow = true;
      this.add(kerb, PAVEMENT_LENGTH_M);
    }
    const yard = new Mesh(new PlaneGeometry(90, GROUND_M).rotateX(-Math.PI / 2), new MeshStandardMaterial({ color: "#7d7268", roughness: 1 }));
    yard.position.y = -0.04;
    yard.receiveShadow = true;
    this.add(yard, PAVEMENT_LENGTH_M);
  }

  private add(mesh: Object3D, every: number): void {
    this.ground.push({ mesh, every });
    this.scene.add(mesh);
  }

  private makeKinds() {
    const parent = this.scene;
    const matte = new MeshStandardMaterial({ vertexColors: true, roughness: 0.75 });
    const satin = new MeshStandardMaterial({ vertexColors: true, roughness: 0.32, metalness: 0.15 });
    const paint = new MeshStandardMaterial({ vertexColors: true, roughness: 0.24, metalness: 0.2, envMapIntensity: 1.2 });
    const wall = new MeshStandardMaterial({ roughness: 0.95 });
    const tiles = new MeshStandardMaterial({ vertexColors: true, roughness: 0.6 });
    const stone = new MeshStandardMaterial({ color: "#d8cdbb", roughness: 0.85 });
    const metal = new MeshStandardMaterial({ color: "#3a3f41", roughness: 0.5, metalness: 0.4 });
    const leaf = new MeshStandardMaterial({ map: this.textures.leaves, alphaTest: 0.42, side: DoubleSide, roughness: 0.8 });
    const bulb = new MeshStandardMaterial({ color: "#fff2d4", emissive: "#ffdf9e", emissiveIntensity: 0.55, roughness: 0.4 });
    const cloth = new MeshStandardMaterial({ color: "#262b33", roughness: 0.9, side: DoubleSide });
    const line = new MeshBasicMaterial({ color: "#d9d3c6" });

    const dark = new MeshBasicMaterial({ map: this.textures.spot, transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 });

    const front = new PlaneGeometry(1, 1).translate(0, 0.5, 0);
    const facade = (name: Facade) => {
      const map = this.textures.facades[name];
      map.repeat.set(0.972, 0.965); // without the pavement below it and the neighbors beside it
      map.offset.set(0.014, 0.034);
      return new Copies(parent, 7, [{ geometry: front, material: new MeshStandardMaterial({ map, roughness: 0.88 }), shadow: false }]);
    };
    const t = tree();
    const p = planter();
    const l = lamp();
    const d = dormer();
    return {
      facades: Object.fromEntries(FACADES.map((name) => [name, facade(name)])) as Record<Facade, Copies>,
      walls: new Copies(parent, 8, [{ geometry: new BoxGeometry(1, 1, 10).translate(0, 0.5, -5.02), material: wall, colored: true }]),
      roofs: new Copies(parent, 8, [{ geometry: roof(), material: tiles, colored: true }]),
      cornices: new Copies(parent, 8, [{ geometry: new BoxGeometry(1, 0.34, 0.42), material: stone }]),
      dormers: new Copies(parent, 24, [
        { geometry: d.walls, material: matte },
        { geometry: d.roof, material: tiles, colored: true },
      ]),
      chimneys: new Copies(parent, 8, [{ geometry: chimney(), material: matte }]),
      pipes: new Copies(parent, 8, [{ geometry: new CylinderGeometry(0.05, 0.05, 1, 6).translate(0, 0.5, 0), material: metal }]),
      awnings: new Copies(parent, 4, [{ geometry: new BoxGeometry(1, 0.03, 1.35).translate(0, 0, 0.67), material: cloth }]),
      valances: new Copies(parent, 4, [{ geometry: new BoxGeometry(1, 0.28, 0.02), material: cloth }]),
      cars: carParts().map(
        (car) =>
          new Copies(parent, 12, [
            { geometry: car.paint, material: paint, colored: true },
            { geometry: car.rest, material: satin },
          ]),
      ),
      spots: new Copies(parent, 14, [{ geometry: new PlaneGeometry(1, 1).rotateX(-Math.PI / 2), material: dark, shadow: false, receive: false }]),
      trees: new Copies(parent, 2, [{ geometry: t.trunk, material: matte }, { geometry: t.leaves, material: leaf, colored: true }]),
      lamps: new Copies(parent, 2, [{ geometry: l.post, material: satin }, { geometry: l.light, material: bulb, shadow: false }]),
      planters: new Copies(parent, 16, [{ geometry: p.pot, material: matte }, { geometry: p.leaves, material: leaf, colored: true }]),
      bikes: new Copies(parent, 12, [{ geometry: bike(), material: satin }]),
      lines: new Copies(parent, 8, [{ geometry: new PlaneGeometry(0.1, 1.9).rotateX(-Math.PI / 2).rotateY(Math.PI / 2), material: line, shadow: false }]),
    };
  }

  private fill(slot: number, segment: number): void {
    const { facades, cars, ...single } = this.kinds;
    const all = [...Object.values(facades), ...cars, ...Object.values(single)] as Copies[];
    for (const kind of all) kind.begin(slot);
    const plan = planSegment(segment);
    for (const house of plan.houses) this.build(house);
    const o = new Object3D();
    const place = (thing: { atM: number; x: number; turn: number; scale: number }, y: number) => {
      o.position.set(thing.x, y, this.z(thing.atM));
      o.rotation.set(0, thing.turn, 0);
      o.scale.setScalar(thing.scale);
      o.updateMatrix();
      return o.matrix;
    };
    const tint = new Color();
    for (const car of plan.cars) {
      cars[car.model]!.add(place(car, 0), PAINT[car.color]);
      o.position.y = 0.014;
      o.scale.set(2.35, 1, 5.2);
      o.updateMatrix();
      single.spots.add(o.matrix);
    }
    for (const tree of plan.trees) single.trees.add(place(tree, 0.13), tint.setHSL(0.16 + tree.tint * 0.07, 0.5, 0.82));
    for (const lamp of plan.lamps) single.lamps.add(place(lamp, 0.13));
    plan.planters.forEach((planter, i) => single.planters.add(place(planter, 0.13), tint.setHSL([0.2, 0.97, 0.13][i % 3]!, 0.55, 0.8)));
    for (const bike of plan.bikes) {
      place(bike, 0.13);
      o.rotation.set(0, bike.turn + Math.PI / 2, 0.1);
      o.updateMatrix();
      single.bikes.add(o.matrix);
    }
    // The ends of each bay.
    for (let block = blockOf(segment * SEGMENT_M) - 1; block <= blockOf(segment * SEGMENT_M) + 1; block += 1) {
      for (const bay of BAYS_AT_M) {
        for (const end of [-3.2, 4.4]) {
          const atM = block * BLOCK_M + bay + end;
          if (Math.floor(atM / SEGMENT_M) !== segment) continue;
          o.position.set(STRIP_RIGHT - 0.05, 0.012, this.z(atM));
          o.rotation.set(0, 0, 0);
          o.scale.setScalar(1);
          o.updateMatrix();
          single.lines.add(o.matrix);
        }
      }
    }
    for (const kind of all) kind.finish();
    this.shadowAt = this.shadowAt === undefined ? undefined : Number.NaN;
  }

  private z(atM: number): number {
    return -(atM - this.base);
  }

  private build({ side, facade, fromM, width, height, roof }: House): void {
    const { facades, walls, roofs, cornices, dormers, chimneys, pipes, awnings, valances } = this.kinds;
    const x = side === "right" ? FACADE_RIGHT : FACADE_LEFT;
    const house = new Object3D();
    house.position.set(x, 0.1, this.z(fromM + width / 2));
    house.rotation.y = side === "right" ? -Math.PI / 2 : Math.PI / 2;
    house.updateMatrix();
    const local = new Object3D();
    const put = (kind: Copies, at: [number, number, number], scale: [number, number, number], color?: Color, tilt = 0) => {
      local.position.set(...at);
      local.scale.set(...scale);
      local.rotation.set(tilt, 0, 0);
      local.updateMatrix();
      kind.add(local.matrix.premultiply(house.matrix), color);
    };
    put(facades[facade], [0, 0, 0], [width, height, 1]);
    put(walls, [0, 0, 0], [width, height, 1], WALLS[facade]);
    put(roofs, [0, height, 0], [width, 1, 1], ROOFS[roof]);
    put(cornices, [0, height - 0.06, 0.16], [width, 1, 1]);
    const n = width > 13.5 ? 3 : 2;
    for (let i = 0; i < n; i += 1) put(dormers, [((i + 0.5) / n - 0.5) * width * 0.86, height + 0.05, 0.05], [1, 1, 1], ROOFS[roof]);
    put(chimneys, [width * 0.28, height + 2.4, -4.4], [1, 1, 1]);
    put(pipes, [width / 2, 0, 0.1], [1, height - 0.1, 1]);
    if (facade === "cafe") {
      put(awnings, [0, height * 0.31, 0], [width * 0.94, 1, 1], undefined, 0.5);
      put(valances, [0, height * 0.31 - 0.75, 1.18], [width * 0.94, 1, 1]);
    }
  }

  // ---- the signs ----

  private makeSign(): SignPost {
    const group = new Group();
    const steel = new MeshStandardMaterial({ color: "#8f9498", roughness: 0.45, metalness: 0.6 });
    const post = new Mesh(new CylinderGeometry(0.032, 0.032, 1, 12).translate(0, 0.5, 0), steel);
    const board = new Mesh(new PlaneGeometry(1, 1), new MeshStandardMaterial());
    const back = new Mesh(new PlaneGeometry(1, 1).rotateY(Math.PI), new MeshStandardMaterial());
    post.castShadow = board.castShadow = true;
    board.receiveShadow = true;
    back.position.z = 0.044;
    board.position.z = 0.046;
    group.add(post, board, back);
    group.rotation.y = SIGN_TURN;
    this.scene.add(group);
    return { group, board, back, post, block: -1, size: { width: 1, height: 1 } };
  }

  private setSign(sign: SignPost, block: number): SignPost {
    const { front, back, def } = this.signMaterial(signOf(block));
    const size = sizeOf(def, front);
    sign.block = block;
    sign.size = size;
    sign.board.material = front;
    sign.back.material = back;
    for (const mesh of [sign.board, sign.back]) {
      mesh.scale.set(size.width, size.height, 1);
      mesh.position.y = SIGN_LOW_M + size.height / 2;
    }
    sign.post.scale.y = SIGN_LOW_M + size.height - 0.06;
    sign.group.position.set(SIGN_X, 0.13, this.z(block * BLOCK_M + SIGN_AT_M));
    sign.group.updateMatrixWorld(true);
    this.shadowAt = this.shadowAt === undefined ? undefined : Number.NaN;
    return sign;
  }

  private signMaterial(id: SignId) {
    const known = this.signMaterials.get(id);
    if (known) return known;
    const def = signDef(id);
    const { front, back } = this.textures.sign(def);
    const made = {
      // A little light of their own, as reflecting signs have.
      front: new MeshStandardMaterial({ map: front, alphaTest: 0.5, color: "#c4c4c4", roughness: 0.6, emissive: "#ffffff", emissiveMap: front, emissiveIntensity: 0.06 }),
      back: new MeshStandardMaterial({ map: back, alphaTest: 0.5, roughness: 0.6, metalness: 0.3 }),
      def,
    };
    this.signMaterials.set(id, made);
    return made;
  }

  private boardOf(sign: SignPost): Board {
    sign.group.updateMatrixWorld(true);
    return {
      center: sign.board.getWorldPosition(new Vector3()),
      facing: new Vector3(Math.sin(SIGN_TURN), 0, Math.cos(SIGN_TURN)),
      ...sign.size,
    };
  }

  private lightFromSky(renderer: WebGLRenderer): void {
    const generator = new PMREMGenerator(renderer);
    const world = new Scene();
    const sky = new Sky(SUN);
    const ground = new Mesh(new PlaneGeometry(400, 400).rotateX(-Math.PI / 2), new MeshBasicMaterial({ color: "#a39a90" }));
    ground.position.y = -3;
    const walls = new Mesh(new BoxGeometry(17, 13, 400), new MeshBasicMaterial({ color: "#a8825c", side: DoubleSide }));
    walls.geometry.deleteAttribute("uv");
    walls.position.y = 3.5;
    // Only the long walls: the sky is open above and at the ends.
    walls.geometry.clearGroups();
    walls.geometry.setDrawRange(0, 12);
    world.add(sky, ground, walls);
    this.scene.environment = generator.fromScene(world, 0.02).texture;
    this.scene.environmentIntensity = 0.5;
    generator.dispose();
  }
}

function sizeOf(def: SignDef, material: MeshStandardMaterial): { width: number; height: number } {
  const image = material.map?.image as { width: number; height: number } | undefined | null;
  if (!image) return { width: 0.7, height: 0.7 * (1 + def.panels.length) };
  const meters = SIGN_ROW_M / 96;
  return { width: image.width * meters, height: image.height * meters };
}

/** The graphics card reads from the bottom up. */
function upright(pixels: Uint8Array): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = PHOTO_PX;
  const ctx = canvas.getContext("2d")!;
  const image = ctx.createImageData(PHOTO_PX, PHOTO_PX);
  const row = 4 * PHOTO_PX;
  for (let y = 0; y < PHOTO_PX; y += 1) {
    image.data.set(pixels.subarray((PHOTO_PX - 1 - y) * row, (PHOTO_PX - y) * row), y * row);
  }
  for (let i = 3; i < image.data.length; i += 4) image.data[i] = 255;
  ctx.putImageData(image, 0, 0);
  return canvas;
}

function makeRenderer(canvas: HTMLCanvasElement): WebGLRenderer {
  const renderer = new WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: "high-performance" });
  renderer.toneMapping = NoToneMapping; // the glass does it
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  return renderer;
}
