import { InstancedMesh, Mesh, Texture, type Object3D } from "three";
import { describe, expect, it } from "vitest";
import { newCar, type Car } from "../sim/car";
import { blurPx, StreetScene, type Renderer } from "./Scene";
import type { Textures } from "./textures";

const at = (distanceM: number, speedKmh = 50): Car => ({ ...newCar(), distanceM, speedKmh, targetKmh: speedKmh });

/** A renderer that only counts what it is given. */
function fakeRenderer() {
  const counts = { meshes: 0, frames: 0 };
  const renderer = {
    render(scene: Object3D) {
      let n = 0;
      scene.traverse((o) => {
        if (o instanceof Mesh) n += 1;
      });
      if (n > 5) counts.meshes = n;
      counts.frames += 1;
    },
    setSize() {},
    setPixelRatio() {},
    setRenderTarget() {},
    clear() {},
    dispose() {},
    shadowMap: { needsUpdate: false },
  };
  return { counts, renderer: renderer as unknown as Renderer };
}

const pair = () => ({ map: new Texture(), bump: new Texture() });
const textures = (): Textures => ({
  road: pair(),
  pavement: pair(),
  facades: { ochre: new Texture(), red: new Texture(), cream: new Texture(), cafe: new Texture(), salmon: new Texture() },
  leaves: new Texture(),
  skyline: new Texture(),
  spot: new Texture(),
  sign: () => ({ front: new Texture(), back: new Texture() }),
});

const canvas = { clientWidth: 1200, clientHeight: 400, width: 1200, height: 400 } as HTMLCanvasElement;

function street() {
  const { counts, renderer } = fakeRenderer();
  return { counts, scene: new StreetScene(canvas, { renderer, textures: textures() }) };
}

describe("the blur of the photo", () => {
  it("starts above 70 km/h", () => {
    expect(blurPx(0)).toBe(0);
    expect(blurPx(50)).toBe(0);
    expect(blurPx(70)).toBe(0);
    expect(blurPx(71)).toBeGreaterThanOrEqual(8);
  });

  it("grows with the speed, up to 24 pixels", () => {
    expect(blurPx(120)).toBeGreaterThan(blurPx(90));
    expect(blurPx(150)).toBeLessThanOrEqual(24);
    expect(blurPx(400)).toBeLessThanOrEqual(24);
  });
});

describe("the street", () => {
  it("uses the same objects again, however far the car has come", () => {
    const { scene, counts } = street();
    scene.render(at(500), "off");
    const early = counts.meshes;
    expect(early).toBeGreaterThan(10);
    for (let m = 500; m <= 5000; m += 10) scene.render(at(m), "off");
    expect(counts.meshes).toBe(early);
    scene.render(at(400_000), "off");
    expect(counts.meshes).toBe(early);
  });

  it("uses few objects, since each is drawn by itself", () => {
    const { scene, counts } = street();
    scene.render(at(0), "off");
    expect(counts.meshes).toBeLessThan(80);
  });

  it("stays close to the origin, where numbers are exact", () => {
    const { scene } = street();
    scene.render(at(400_000), "off");
    scene.scene.updateMatrixWorld(true);
    scene.scene.traverse((o) => {
      if (!(o instanceof InstancedMesh)) return;
      const z = o.instanceMatrix.array[14]!;
      expect(Math.abs(z)).toBeLessThan(6000);
    });
  });

  it("shows the sign of the block ahead and of the one just passed", () => {
    const { scene } = street();
    scene.render(at(395), "off");
    expect(scene.signBlocks()).toEqual([1, 2]);
    scene.render(at(430), "off");
    expect(scene.signBlocks()).toEqual([1, 2]);
    scene.render(at(520), "off");
    expect(scene.signBlocks()).toEqual([2, 3]);
  });

  it("draws nothing when it has no size", () => {
    const { renderer, counts } = fakeRenderer();
    const scene = new StreetScene({ clientWidth: 0, clientHeight: 0 } as HTMLCanvasElement, { renderer, textures: textures() });
    scene.render(at(0), "off");
    expect(counts.frames).toBe(0);
  });
});
