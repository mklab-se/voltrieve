/** The sky, and the rain in the air. */
import {
  BackSide,
  BufferGeometry,
  Color,
  Float32BufferAttribute,
  LineSegments,
  Mesh,
  ShaderMaterial,
  SphereGeometry,
  Vector3,
} from "three";
import { random } from "./plan";

export const HORIZON = { dry: new Color("#f4e6d0"), rain: new Color("#a9b1b6") };
const ZENITH = { dry: new Color("#5fa4dc"), rain: new Color("#8b969e") };

export class Sky extends Mesh<SphereGeometry, ShaderMaterial> {
  constructor(sun: Vector3) {
    super(
      new SphereGeometry(900, 32, 20),
      new ShaderMaterial({
        side: BackSide,
        depthWrite: false,
        uniforms: {
          uHorizon: { value: HORIZON.dry.clone() },
          uZenith: { value: ZENITH.dry.clone() },
          uSun: { value: sun },
          uRain: { value: 0 },
        },
        vertexShader: /* glsl */ `
          varying vec3 vDir;
          void main() {
            vDir = position;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }`,
        fragmentShader: /* glsl */ `
          uniform vec3 uHorizon, uZenith, uSun;
          uniform float uRain;
          varying vec3 vDir;
          float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
          float noise(vec2 p) {
            vec2 i = floor(p), f = fract(p);
            f = f * f * (3.0 - 2.0 * f);
            return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + 1.0), f.x), f.y);
          }
          float fbm(vec2 p) {
            float s = 0.0, a = 0.5;
            for (int i = 0; i < 5; i++) { s += a * noise(p); p = p * 2.03 + 7.1; a *= 0.5; }
            return s;
          }
          void main() {
            vec3 d = normalize(vDir);
            float up = max(d.y, 0.0);
            vec3 c = mix(uHorizon, uZenith, 1.0 - pow(1.0 - min(up * 2.6, 1.0), 2.0));
            // Thin, high clouds, stretched along the street.
            vec2 q = d.xz / (up + 0.16);
            float cloud = smoothstep(0.5, 0.82, fbm(q * vec2(1.5, 0.8) + 3.0)) * smoothstep(0.03, 0.3, up);
            c = mix(c, vec3(1.0, 0.95, 0.88) * 1.05, cloud * 0.55 * (1.0 - uRain));
            float heavy = fbm(q * 0.9);
            c = mix(c, c * (0.72 + 0.5 * heavy), uRain);
            float glow = max(dot(d, uSun), 0.0);
            c += vec3(1.0, 0.72, 0.4) * (pow(glow, 6.0) * 0.2 + pow(glow, 90.0) * 0.8) * (1.0 - uRain);
            gl_FragColor = vec4(c, 1.0);
          }`,
      }),
    );
    this.frustumCulled = false;
    this.renderOrder = -2;
  }

  set rain(share: number) {
    const u = this.material.uniforms;
    u.uHorizon!.value.lerpColors(HORIZON.dry, HORIZON.rain, share);
    u.uZenith!.value.lerpColors(ZENITH.dry, ZENITH.rain, share);
    u.uRain!.value = share;
  }
}

const DROPS = 900;
const FIELD = new Vector3(26, 12, 44);

/** Streaks in a box in front of the car. They fall, and come nearer with the speed of the car. */
export class Rain extends LineSegments<BufferGeometry, ShaderMaterial> {
  constructor() {
    const rnd = random(123);
    const positions: number[] = [];
    const ends: number[] = [];
    for (let i = 0; i < DROPS; i += 1) {
      const p = [rnd() * FIELD.x, rnd() * FIELD.y, rnd() * FIELD.z];
      positions.push(...p, ...p);
      ends.push(0, 1);
    }
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(positions, 3));
    g.setAttribute("end", new Float32BufferAttribute(ends, 1));
    super(
      g,
      new ShaderMaterial({
        transparent: true,
        depthWrite: false,
        uniforms: {
          uField: { value: FIELD },
          uMoved: { value: new Vector3() },
          uStreak: { value: new Vector3() },
          uShare: { value: 0 },
        },
        vertexShader: /* glsl */ `
          attribute float end;
          uniform vec3 uField, uMoved, uStreak;
          varying float vAlpha;
          void main() {
            vec3 p = mod(position + uMoved, uField) - vec3(uField.x * 0.5, 1.0, uField.z);
            vAlpha = smoothstep(-uField.z, -uField.z * 0.5, p.z) * (1.0 - end * 0.7);
            gl_Position = projectionMatrix * modelViewMatrix * vec4(p + uStreak * end, 1.0);
          }`,
        fragmentShader: /* glsl */ `
          uniform float uShare;
          varying float vAlpha;
          void main() { gl_FragColor = vec4(vec3(0.85, 0.9, 0.95), vAlpha * uShare * 0.3); }`,
      }),
    );
    this.frustumCulled = false;
  }

  step(dtSeconds: number, speedMs: number, share: number): void {
    this.visible = share > 0.01;
    const u = this.material.uniforms;
    const moved = u.uMoved!.value as Vector3;
    moved.y = (moved.y - 9 * dtSeconds) % FIELD.y;
    moved.z = (moved.z + speedMs * dtSeconds) % FIELD.z;
    u.uStreak!.value.set(0.02, 0.36, -speedMs * 0.035);
    u.uShare!.value = share;
  }
}
