import { describe, expect, it, vi } from "vitest";
import { CAR_TOOLS, runCarTool, type CarControls } from "./tools";
import tools from "./tools.json";

function controls(over: Partial<CarControls> = {}) {
  return {
    speedKmh: vi.fn(() => 60),
    setSpeed: vi.fn(),
    setWipers: vi.fn(),
    honk: vi.fn(async () => true),
    park: vi.fn(async () => ({ block: 3, askedBlock: undefined as number | undefined })),
    signPhoto: vi.fn(async (): Promise<{ id?: string; message: string }> => ({ id: "att1", message: "The photo is attached. It shows the sign of block 4." })),
    ...over,
  };
}

const call = (name: string, args: unknown = "{}") => ({ call_id: "1", name, arguments: args });

async function output(name: string, args?: unknown, car: CarControls = controls()): Promise<string> {
  return (await runCarTool(call(name, args), car)).output;
}

describe("the car's tools", () => {
  it("offers the list that the contract test compares", () => {
    expect(CAR_TOOLS).toBe(tools);
    expect(CAR_TOOLS.map((t) => t.name)).toEqual(["set_speed", "set_wipers", "honk", "park", "get_sign_photo"]);
  });

  it.each([
    [90, "The car is accelerating to 90 km/h."],
    [30, "The car is braking to 30 km/h."],
    [60, "The car is already at 60 km/h."],
  ])("sets the speed to %i", async (kmh, text) => {
    const car = controls();
    expect(await output("set_speed", `{"kmh":${kmh}}`, car)).toBe(text);
    expect(car.setSpeed).toHaveBeenCalledExactlyOnceWith(kmh);
  });

  it("stops at 0 and goes as fast as the car can", async () => {
    expect(await output("set_speed", '{"kmh":0}')).toBe("The car is braking to 0 km/h.");
    expect(await output("set_speed", { kmh: 150 })).toBe("The car is accelerating to 150 km/h.");
  });

  it.each(["off", "slow", "fast"])("sets the wipers to %s", async (mode) => {
    const car = controls();
    expect(await output("set_wipers", JSON.stringify({ mode }), car)).toBe(`The wipers are ${mode}.`);
    expect(car.setWipers).toHaveBeenCalledExactlyOnceWith(mode);
  });

  it("honks, and says so when the browser did not let it", async () => {
    const car = controls();
    expect(await output("honk", '{"times":2}', car)).toBe("Honked 2 time(s).");
    expect(car.honk).toHaveBeenCalledExactlyOnceWith(2);
    const silent = controls({ honk: async () => false });
    expect(await output("honk", '{"times":2}', silent)).toBe("The browser blocked the sound. Click in the car first.");
  });

  it.each([
    [3, 3, "Parked, in the block you asked about."],
    [4, 3, "Parked in another block. Another sign applies here: ask again before you leave the car."],
    [0, 0, "Parked, in the block you asked about."],
    [3, undefined, "Parked."],
  ])("parks in block %s when the question was about block %s", async (block, askedBlock, text) => {
    expect(await output("park", "{}", controls({ park: async () => ({ block, askedBlock }) }))).toBe(text);
  });

  it("waits until the car has stopped", async () => {
    let stopped = () => {};
    const car = controls({ park: () => new Promise((resolve) => (stopped = () => resolve({ block: 1 }))) });
    const done = vi.fn();
    const result = output("park", "{}", car).then(done);
    await new Promise((r) => setTimeout(r, 5));
    expect(done).not.toHaveBeenCalled();
    stopped();
    await result;
    expect(done).toHaveBeenCalledWith("Parked.");
  });

  it("attaches the photo of the sign", async () => {
    expect(await runCarTool(call("get_sign_photo"), controls())).toEqual({
      output: "The photo is attached. It shows the sign of block 4.",
      attachments: ["att1"],
    });
  });

  it.each(["The photo is blurred. Hold still or slow down and take a new one.", "No parking sign has been passed yet."])(
    "gives the message when there is no photo: %s",
    async (message) => {
      const car = controls({ signPhoto: async () => ({ message }) });
      expect(await runCarTool(call("get_sign_photo"), car)).toEqual({ output: message });
    },
  );

  it.each(["", "{}", null, undefined, {}, "null"])("takes %j where there are no arguments", async (args) => {
    expect(await output("park", args)).toBe("Parked.");
  });

  it.each([
    ["set_speed", '{"kmh":151}'],
    ["set_speed", '{"kmh":-1}'],
    ["set_speed", '{"kmh":1.5}'],
    ["set_speed", '{"kmh":"90"}'],
    ["set_speed", "{}"],
    ["set_speed", "{oops"],
    ["set_wipers", '{"mode":"turbo"}'],
    ["set_wipers", '{"mode":["off"]}'],
    ["honk", '{"times":0}'],
    ["honk", '{"times":6}'],
    ["honk", '{"times":true}'],
    ["park", "{oops"],
    ["park", "[1]"],
    ["get_sign_photo", '"now"'],
    ["open_trunk", "{}"],
    ["toString", "{}"],
    ["", "{}"],
  ])("refuses %s %s and changes nothing", async (name, args) => {
    const car = controls();
    const result = await runCarTool(call(name, args), car);
    expect(result.output).toMatch(/^Error: /);
    expect(result.attachments).toBeUndefined();
    for (const control of [car.setSpeed, car.setWipers, car.honk, car.park, car.signPhoto]) expect(control).not.toHaveBeenCalled();
  });

  it("gives a control that fails as text", async () => {
    const car = controls({ park: () => Promise.reject(new Error("The parking was given up.")) });
    expect(await output("park", "{}", car)).toBe("Error: The parking was given up.");
  });
});
