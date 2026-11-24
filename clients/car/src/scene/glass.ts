/**
 * The last step of a picture: the windscreen with its rain and wipers, the mirror, and the colors as a
 * screen wants them. It also develops the photo of a sign, without the glass.
 */
import {
  Mesh,
  NoBlending,
  OrthographicCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  Vector4,
  type Texture,
} from "three";
import { MIRROR, PICTURE, WINDSCREEN, type Box } from "../layout";
import { wiperPosition, type Wipers } from "./wipers";

const VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = vec4(position.xy, 0.0, 1.0);
}`;

const FRAGMENT = /* glsl */ `
#include <common>
#include <tonemapping_pars_fragment>
uniform sampler2D uScene;
uniform sampler2D uMirror;
uniform bool uGlass;
uniform bool uMirrorOn;
uniform vec4 uWindow;  // the windscreen in pixels of the cockpit picture: x, y, width, height
uniform vec4 uMirrorAt;
uniform float uTime;
uniform float uRain;   // 0 to 1
uniform float uSpeed;  // 0 to 1
uniform float uPhase;
uniform float uCycle;
uniform float uSweepS;
uniform float uWiper;
uniform float uPixel;  // pixels of the picture in one pixel of the screen
varying vec2 vUv;

const vec2 PIVOT_A = vec2(392.0, 524.0);
const vec2 PIVOT_B = vec2(936.0, 524.0);
const float REACH = 440.0;
const float INNER = 70.0;
const float A0 = 0.045;
const float A1 = 1.98;

float hash(vec2 p) {
  vec3 q = fract(vec3(p.xyx) * 0.1031);
  q += dot(q, q.yzx + 33.33);
  return fract((q.x + q.y) * q.z);
}

vec2 sceneUv(vec2 p) {
  vec2 q = (p - uWindow.xy) / uWindow.zw;
  return vec2(q.x, 1.0 - q.y);
}

// The seconds since a wiper went over this place. Very long where none of them reaches.
float sinceWiped(vec2 p, vec2 pivot) {
  vec2 d = p - pivot;
  float r = length(d);
  if (r < INNER || r > REACH) return 1e3;
  float s = (atan(-d.y, d.x) - A0) / (A1 - A0);
  if (s < 0.0 || s > 1.0) return 1e3;
  float up = acos(1.0 - 2.0 * s) / PI2;
  float down = 1.0 - up;
  float phase = uPhase;
  float last = phase >= down ? down : phase >= up ? up : down - uCycle;
  return (phase - last) * uSweepS;
}

// Drops on the glass: each lives in its own cell for a while, and is wiped away.
vec3 drops(vec2 p, float cell, float size, float seed, float share) {
  vec2 id = floor(p / cell);
  float h = hash(id + seed);
  float life = 5.0 + 5.0 * hash(id * 1.7 + seed);
  float t = uTime / life + h;
  float n = floor(t);
  float age = fract(t) * life;
  if (hash(id + n * 7.13 + seed) > share * uRain) return vec3(0.0);
  vec2 jitter = vec2(hash(id + n + seed + 3.1), hash(id + n * 2.0 + seed + 8.7));
  float radius = cell * size * (0.45 + 0.55 * hash(id + n + 5.5 + seed));
  vec2 center = (id + 0.5 + (jitter - 0.5) * (1.0 - 2.0 * radius / cell)) * cell;
  if (uPhase > 0.0 || uRain > 0.0) {
    float since = min(sinceWiped(center, PIVOT_A), sinceWiped(center, PIVOT_B));
    if (age > since) return vec3(0.0);
  }
  vec2 d = (p - center) / radius;
  d.x *= 1.0 + 0.25 * (jitter.x - 0.5);
  d.y *= 0.92 - 0.12 * uSpeed;
  float m = length(d);
  float grow = smoothstep(0.0, 0.12, age) * (1.0 - smoothstep(life - 0.5, life, age));
  float inside = (1.0 - smoothstep(0.82, 1.0, m)) * grow;
  return vec3(d * smoothstep(0.1, 1.0, m), inside);
}

float segment(vec2 p, vec2 a, vec2 b) {
  vec2 pa = p - a, ba = b - a;
  return length(pa - ba * clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0));
}

// The arm and the blade, as seen against the light.
float wiper(vec2 p, vec2 pivot) {
  float a = mix(A0, A1, uWiper);
  vec2 dir = vec2(cos(a), -sin(a));
  vec2 n = vec2(-dir.y, dir.x);
  vec2 d = p - pivot;
  float along = clamp(dot(d, dir) / REACH, 0.0, 1.0);
  float arm = segment(p, pivot, pivot + dir * REACH * 0.62 + n * 5.0) - mix(6.5, 3.0, along);
  float blade = segment(p, pivot + dir * INNER, pivot + dir * REACH) - 2.6;
  float frame = segment(p, pivot + dir * REACH * 0.3 + n * 2.5, pivot + dir * REACH * 0.92 + n * 2.5) - 3.1;
  return 1.0 - smoothstep(-uPixel, uPixel, min(min(arm, blade), frame));
}

vec3 develop(vec3 color) {
  return sRGBTransferOETF(vec4(ACESFilmicToneMapping(color), 1.0)).rgb;
}

void main() {
  if (!uGlass) {
    gl_FragColor = vec4(develop(texture2D(uScene, vUv).rgb), 1.0);
    return;
  }
  vec2 p = uWindow.xy + vec2(vUv.x, 1.0 - vUv.y) * uWindow.zw;
  vec2 m = (p - uMirrorAt.xy) / uMirrorAt.zw;
  if (m.x > 0.0 && m.x < 1.0 && m.y > 0.0 && m.y < 1.0) {
    // The mirror is in front of the glass. It shows the other way around, and a little darker.
    vec3 back = uMirrorOn ? texture2D(uMirror, vec2(1.0 - m.x, 1.0 - m.y)).rgb : vec3(0.18, 0.2, 0.22);
    back = develop(back * 0.72);
    float edge = smoothstep(0.0, 0.1, m.y) * smoothstep(0.0, 0.04, m.x) * smoothstep(0.0, 0.04, 1.0 - m.x);
    back = mix(back * 0.55, back, edge) + 0.05 * smoothstep(0.6, 0.0, abs(m.x * 0.7 + m.y * 0.5 - 0.45));
    gl_FragColor = vec4(back, 1.0);
    return;
  }

  vec2 offset = vec2(0.0);
  float wet = 0.0, rim = 0.0, shine = 0.0;
  if (uRain > 0.01) {
    vec3 a = drops(p, 46.0, 0.2, 1.0, 0.45);
    vec3 b = drops(p + 13.0, 21.0, 0.3, 31.0, 0.72);
    vec3 c = drops(p + 5.0, 10.0, 0.34, 67.0, 0.55);
    vec3 d = a.z > 0.0 ? a : b.z > 0.0 ? b : c;
    wet = d.z;
    offset = -d.xy * (a.z > 0.0 ? 64.0 : b.z > 0.0 ? 36.0 : 16.0);
    rim = smoothstep(0.3, 1.0, length(d.xy)) * wet * (0.35 + 0.65 * smoothstep(-0.6, 0.8, d.y));
    shine = wet * smoothstep(0.4, 0.95, dot(d.xy, normalize(vec2(-0.5, -0.85))));
  }
  vec3 color = texture2D(uScene, sceneUv(p + offset * wet)).rgb;
  if (uRain > 0.01) {
    // A thin film between the drops blurs the street a little until the next sweep.
    float since = min(sinceWiped(p, PIVOT_A), sinceWiped(p, PIVOT_B));
    float film = uRain * smoothstep(0.0, 1.6, since) * 0.55;
    vec3 soft = vec3(0.0);
    for (int i = 0; i < 6; i++) {
      float q = float(i) * 1.0472;
      soft += texture2D(uScene, sceneUv(p + vec2(cos(q), sin(q)) * 5.0)).rgb;
    }
    color = mix(color, soft / 6.0, film * (1.0 - wet));
    color = color * (1.0 - rim * 0.36) + shine * 0.36;
  }
  color = develop(color);

  // The glass: a soft reflection of the dashboard at the bottom, and a tinted band at the top.
  vec2 w = (p - uWindow.xy) / uWindow.zw;
  color += vec3(0.034, 0.033, 0.031) * smoothstep(0.62, 1.0, w.y);
  color *= mix(vec3(0.80, 0.86, 0.93), vec3(1.0), smoothstep(0.02, 0.2, w.y));

  float dark = max(wiper(p, PIVOT_A), wiper(p, PIVOT_B));
  color = mix(color, vec3(0.035, 0.037, 0.04), dark * 0.96);
  gl_FragColor = vec4(color, 1.0);
}`;

const pixels = ({ left, top, width, height }: Box) =>
  new Vector4((left / 100) * PICTURE.width, (top / 100) * PICTURE.height, (width / 100) * PICTURE.width, (height / 100) * PICTURE.height);

export interface GlassState {
  wipers: Wipers;
  timeS: number;
  rain: number;
  speed: number;
  /** Pixels of the cockpit picture in one pixel of the screen. */
  pixel: number;
}

export class Glass {
  readonly scene = new Scene();
  readonly camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private readonly material: ShaderMaterial;
  private readonly quad: Mesh;

  constructor() {
    this.material = new ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      uniforms: {
        uScene: { value: null },
        uMirror: { value: null },
        uGlass: { value: true },
        uMirrorOn: { value: true },
        uWindow: { value: pixels(WINDSCREEN) },
        uMirrorAt: { value: pixels(MIRROR) },
        uTime: { value: 0 },
        uRain: { value: 0 },
        uSpeed: { value: 0 },
        uPhase: { value: 0 },
        uCycle: { value: 1 },
        uSweepS: { value: 1 },
        uWiper: { value: 0 },
        uPixel: { value: 1 },
        toneMappingExposure: { value: 1 },
      },
      depthTest: false,
      depthWrite: false,
      blending: NoBlending,
      toneMapped: false,
    });
    this.quad = new Mesh(new PlaneGeometry(2, 2), this.material);
    this.quad.frustumCulled = false;
    this.scene.add(this.quad);
  }

  set exposure(value: number) {
    this.material.uniforms.toneMappingExposure!.value = value;
  }

  /** The windscreen over this picture of the street. */
  windscreen(street: Texture, mirror: Texture | undefined, state: GlassState): void {
    const u = this.material.uniforms;
    u.uGlass!.value = true;
    u.uScene!.value = street;
    u.uMirror!.value = mirror ?? null;
    u.uMirrorOn!.value = mirror !== undefined;
    u.uTime!.value = state.timeS;
    u.uRain!.value = state.rain;
    u.uSpeed!.value = state.speed;
    u.uPhase!.value = state.wipers.phase;
    u.uCycle!.value = state.wipers.cycle;
    u.uSweepS!.value = state.wipers.sweepS;
    u.uWiper!.value = wiperPosition(state.wipers.phase);
    u.uPixel!.value = state.pixel;
  }

  /** The picture alone, as a photo. */
  plain(picture: Texture): void {
    this.material.uniforms.uGlass!.value = false;
    this.material.uniforms.uScene!.value = picture;
  }

  dispose(): void {
    this.quad.geometry.dispose();
    this.material.dispose();
  }
}

