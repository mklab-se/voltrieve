import { describe, expect, it, vi } from "vitest";
import { START_ODOMETER_KM } from "./context";
import { carState, commandText, handleCommand, standsStill, type Code, type Command, type RemoteCar } from "./remote";
import { newCar, setSpeed, startParking, step, type Car } from "./sim/car";
import { CAR_TOOLS } from "./tools";

const command = (name: string, args: Record<string, unknown> = {}, app = "Claude"): Command => ({ id: "1", name, arguments: args, app });

function car(changes: Partial<Car> = {}, on = true, heard = true) {
  const controls = { honk: vi.fn(async () => heard), setWipers: vi.fn(), flashLights: vi.fn() };
  // What a tool of Runa's could do must not be there, even when it is.
  const drives = { setSpeed: vi.fn(), park: vi.fn(), signPhoto: vi.fn() };
  const remote: RemoteCar = { on, car: { ...newCar(), ...changes }, controls: Object.assign(controls, drives) };
  const untouched = () => Object.values({ ...controls, ...drives }).every((f) => f.mock.calls.length === 0);
  return { remote, controls, drives, untouched };
}

const GOOD = [command("honk", { times: 3 }), command("flash_lights", { times: 5 }), command("set_wipers", { mode: "fast" })];

describe("a command", () => {
  it("is done when the car stands still", async () => {
    const { remote, controls } = car();
    for (const c of GOOD) expect(await handleCommand(c, remote)).toBe("done");
    expect(controls.honk).toHaveBeenCalledWith(3);
    expect(controls.flashLights).toHaveBeenCalledWith(5);
    expect(controls.setWipers).toHaveBeenCalledWith("fast");
  });

  it("is not done when remote control is off", async () => {
    const { remote, untouched } = car({}, false);
    for (const c of GOOD) expect(await handleCommand(c, remote)).toBe("off");
    expect(untouched()).toBe(true);
  });

  it.each<[string, Partial<Car>]>([
    ["moves", { speedKmh: 0.1, targetKmh: 0 }],
    ["is about to move", { targetKmh: 30 }],
    ["brakes", { speedKmh: 50, targetKmh: 0 }],
    ["parks", { mode: "parking", targetKmh: 15, bayM: 20 }],
    ["parks and stands for a moment", { mode: "parking", targetKmh: 0, bayM: 20 }],
    ["leaves its bay", { mode: "leaving", lateral: 0.5 }],
  ])("is not done when the car %s", async (_, changes) => {
    const { remote, untouched } = car(changes);
    for (const c of GOOD) expect(await handleCommand(c, remote)).toBe("moving");
    expect(untouched()).toBe(true);
  });

  it.each(["set_speed", "park", "get_sign_photo", "", "HONK", "toString", "__proto__", "constructor"])("named %j is unknown", async (name) => {
    const { remote, untouched } = car();
    expect(await handleCommand(command(name, { kmh: 50, times: 1, mode: "off" }), remote)).toBe("unknown");
    expect(untouched()).toBe(true);
  });

  it("knows none of the ways of Runa to drive, park or look", () => {
    const driving = CAR_TOOLS.map((t) => t.name).filter((n) => !["honk", "set_wipers"].includes(n));
    expect(driving).toEqual(["set_speed", "park", "get_sign_photo"]);
  });

  it.each([
    command("honk"),
    command("honk", { times: 0 }),
    command("honk", { times: 4 }),
    command("honk", { times: 1.5 }),
    command("honk", { times: "2" }),
    command("flash_lights", { times: 6 }),
    command("flash_lights", { times: Number.NaN }),
    command("set_wipers", { mode: "max" }),
    command("set_wipers", { mode: ["off"] }),
  ])("with bad arguments is not done (%j)", async (c) => {
    const { remote, untouched } = car();
    expect(await handleCommand(c, remote)).toBe("bad_arguments");
    expect(untouched()).toBe(true);
  });

  it("says when the browser blocked the sound, and when something broke", async () => {
    expect(await handleCommand(GOOD[0]!, car({}, true, false).remote)).toBe("no_sound");
    const { remote } = car();
    remote.controls.flashLights = () => {
      throw new Error("broken");
    };
    expect(await handleCommand(GOOD[1]!, remote)).toBe("failed");
  });
});

describe("standing still", () => {
  it("is what a new car does, and a parked one, and not one on its way", () => {
    let c = newCar();
    expect(standsStill(c)).toBe(true);
    c = startParking(c);
    expect(standsStill(c)).toBe(false);
    for (let i = 0; i < 2000 && c.mode !== "parked"; i += 1) c = step(c, 0.1);
    expect(standsStill(c)).toBe(true);
    c = setSpeed(c, 30);
    expect(standsStill(c)).toBe(false);
  });

  it("is told with the wipers and whole kilometers, and nothing else", () => {
    expect(carState({ ...newCar(), distanceM: 2900 }, "slow")).toEqual({ parked: true, odometer_km: START_ODOMETER_KM + 2, wipers: "slow" });
    expect(carState({ ...newCar(), speedKmh: 20 }, "off").parked).toBe(false);
  });
});

describe("what the driver is told", () => {
  it.each<[Command, Code, string]>([
    [GOOD[0]!, "done", "Claude honked the horn."],
    [GOOD[1]!, "done", "Claude flashed the lights."],
    [GOOD[2]!, "done", "Claude set the wipers to fast."],
    [command("set_wipers", { mode: "off" }, "ChatGPT"), "done", "ChatGPT turned the wipers off."],
    [GOOD[0]!, "moving", "Claude wanted to honk the horn: the car is not parked."],
    [GOOD[0]!, "off", "Claude wanted to honk the horn: remote control is off."],
    [command("<b>park</b>"), "unknown", "Claude wanted to do something else: the car does not do that."],
    [command("toString"), "unknown", "Claude wanted to do something else: the car does not do that."],
    [command("honk", { times: 9 }, "  "), "bad_arguments", "An app wanted to honk the horn: the car did not understand it."],
  ])("%#", (c, code, text) => {
    expect(commandText(c, code)).toBe(text);
  });

  it("has a short name of the app, and no words of the command", () => {
    expect(commandText(command("honk", { times: "MARKER" }, "x".repeat(500)), "bad_arguments").length).toBeLessThan(120);
    expect(commandText(command("MARKER", { mode: "MARKER" }), "unknown")).not.toContain("MARKER");
  });
});
