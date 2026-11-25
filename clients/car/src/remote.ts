/** What the driver's own AI may ask of the car, and how the car decides. The car decides last: it does
 * nothing while it moves, and nothing that is not in this file. */
import { START_ODOMETER_KM } from "./context";
import type { Car } from "./sim/car";
import { isWhole, WIPER_MODES, type CarControls, type WiperMode } from "./tools";

export interface Command {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  /** The name of the app that asks, from the API's own list. */
  app: string;
}

/** The codes of the API. It turns them into its own words. */
export type Code = "done" | "off" | "moving" | "unknown" | "bad_arguments" | "no_sound" | "failed";

/** The part of the car that can be used from afar. Nothing in it drives, parks or sees. */
export interface RemoteControls extends Pick<CarControls, "honk" | "setWipers"> {
  flashLights(times: number): void;
}

export interface RemoteCar {
  /** Remote control is on. */
  on: boolean;
  car: Car;
  controls: RemoteControls;
}

export const MAX_HONKS = 3;
export const MAX_FLASHES = 5;

/** It stands still, and nothing it does by itself is about to change that. */
export function standsStill({ speedKmh, targetKmh, mode }: Car): boolean {
  return speedKmh === 0 && (mode === "parked" || (mode === "driving" && targetKmh === 0));
}

/** What to do for a command, or the code for why not. */
function plan({ name, arguments: args }: Command, { on, car, controls }: RemoteCar): Code | (() => Code | Promise<Code>) {
  if (!on) return "off";
  if (!standsStill(car)) return "moving";
  const { times, mode } = args;
  switch (name) {
    case "honk":
      if (!isWhole(times, 1, MAX_HONKS)) return "bad_arguments";
      return async () => ((await controls.honk(times)) ? "done" : "no_sound");
    case "flash_lights":
      if (!isWhole(times, 1, MAX_FLASHES)) return "bad_arguments";
      return () => (controls.flashLights(times), "done");
    case "set_wipers": {
      const wipers = WIPER_MODES.find((m) => m === mode);
      if (!wipers) return "bad_arguments";
      return () => (controls.setWipers(wipers), "done");
    }
    default:
      return "unknown";
  }
}

export async function handleCommand(command: Command, remote: RemoteCar): Promise<Code> {
  const todo = plan(command, remote);
  if (typeof todo === "string") return todo;
  try {
    return await todo();
  } catch {
    return "failed";
  }
}

const DONE: Record<string, (args: Record<string, unknown>) => string> = {
  honk: () => "honked the horn",
  flash_lights: () => "flashed the lights",
  set_wipers: ({ mode }) => (mode === "off" ? "turned the wipers off" : `set the wipers to ${mode === "fast" ? "fast" : "slow"}`),
};

const WANTED: Record<string, string> = {
  honk: "honk the horn",
  flash_lights: "flash the lights",
  set_wipers: "set the wipers",
};

const WHY: Record<Exclude<Code, "done">, string> = {
  off: "remote control is off",
  moving: "the car is not parked",
  unknown: "the car does not do that",
  bad_arguments: "the car did not understand it",
  no_sound: "the browser blocked the sound, click in the car first",
  failed: "it did not work",
};

/** What the driver is told. Only the name of the app comes from outside. */
export function commandText({ name, arguments: args, app }: Command, code: Code): string {
  const who = app.trim().slice(0, 40) || "An app";
  if (code === "done" && Object.hasOwn(DONE, name)) return `${who} ${DONE[name]!(args)}.`;
  const wanted = Object.hasOwn(WANTED, name) ? WANTED[name]! : "do something else";
  return `${who} wanted to ${wanted}: ${WHY[code === "done" ? "failed" : code]}.`;
}

/** What the car tells the API about itself. */
export interface CarState {
  parked: boolean;
  odometer_km: number;
  wipers: WiperMode;
}

export function carState(car: Car, wipers: WiperMode): CarState {
  return { parked: standsStill(car), odometer_km: START_ODOMETER_KM + Math.floor(car.distanceM / 1000), wipers };
}
