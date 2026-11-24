import { InstancedMesh, Mesh, Texture, type Object3D } from "three";
import { describe, expect, it, vi } from "vitest";
import { newCar, type Car } from "../sim/car";
import { blurPx, StreetScene, type Renderer } from "./Scene";
import type { Textures } from "./textures";

const at = (distanceM: number, speedKmh = 50): Car => ({ ...newCar(), distanceM, speedKmh, targetKmh: speedKmh });

/** A renderer that only counts what it is given. */
function fakeRenderer() {
  const counts = { meshes: 0, frames: 0 };
  const gl = { lost: false };
  const renderer = {
    getContext: () => ({ isContextLost: () => gl.lost }),
    readRenderTargetPixelsAsync: vi.fn(async (): Promise<unknown> => undefined),
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
    setRenderTarget: vi.fn(),
    clear() {},
    dispose() {},
    shadowMap: { needsUpdate: false },
  };
  return { counts, gl, fake: renderer, renderer: renderer as unknown as Renderer };
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

const canvasOf = (clientWidth: number, clientHeight: number) =>
  Object.assign(new EventTarget(), { clientWidth, clientHeight }) as unknown as HTMLCanvasElement;

function street() {
  const { renderer, ...rest } = fakeRenderer();
  const canvas = canvasOf(1200, 400);
  const skyLight = vi.fn(() => new Texture());
  return { ...rest, canvas, skyLight, scene: new StreetScene(canvas, { renderer, textures: textures(), skyLight }) };
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
    const scene = new StreetScene(canvasOf(0, 0), { renderer, textures: textures() });
    scene.render(at(0), "off");
    expect(counts.frames).toBe(0);
  });
});

describe("the camera of the signs", () => {
  it("takes no photo without the graphics card", async () => {
    const { scene, gl, fake } = street();
    gl.lost = true;
    await expect(scene.photo(at(420), 1)).rejects.toThrow("The photo could not be taken.");
    expect(fake.readRenderTargetPixelsAsync).not.toHaveBeenCalled();
  });

  it("gives no black photo when the graphics card is lost meanwhile, or nothing was read", async () => {
    const { scene, gl, fake } = street();
    await expect(scene.photo(at(420), 1)).rejects.toThrow("The photo could not be taken."); // nothing read
    fake.readRenderTargetPixelsAsync.mockImplementation(async (...args: unknown[]) => {
      gl.lost = true;
      return args[5];
    });
    await expect(scene.photo(at(420), 1)).rejects.toThrow("The photo could not be taken.");
  });

  it("leaves the street as it was when drawing fails", async () => {
    const { scene, fake } = street();
    scene.render(at(420), "off");
    const children = scene.scene.children.length;
    const draw = fake.render;
    fake.render = () => {
      throw new Error("no draw");
    };
    await expect(scene.photo(at(420), 1)).rejects.toThrow("no draw");
    fake.render = draw;
    expect(scene.scene.children).toHaveLength(children);
    expect(fake.setRenderTarget).toHaveBeenLastCalledWith(null);
    let hidden = 0;
    scene.scene.traverse((o) => {
      if (o instanceof Mesh && !o.visible) hidden += 1;
    });
    expect(hidden).toBe(0);
  });
});

describe("a graphics card that comes back", () => {
  it("gets the light of the sky and the shadows again", () => {
    const { scene, canvas, skyLight, fake } = street();
    scene.render(at(100), "off");
    expect(skyLight).toHaveBeenCalledTimes(1);
    const old = scene.scene.environment!;
    const dropped = vi.spyOn(old, "dispose");
    fake.shadowMap.needsUpdate = false;
    scene.render(at(100), "off");
    expect(fake.shadowMap.needsUpdate).toBe(false); // the car stands still
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(skyLight).toHaveBeenCalledTimes(2);
    expect(dropped).toHaveBeenCalled();
    expect(scene.scene.environment).not.toBe(old);
    scene.render(at(100), "off");
    expect(fake.shadowMap.needsUpdate).toBe(true);
  });

  it("is not listened to after the end", () => {
    const { scene, canvas, skyLight } = street();
    scene.dispose();
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(skyLight).toHaveBeenCalledTimes(1);
  });
});
