import { TOP_KMH } from "./sim/car";
import tools from "./tools.json";
import type { ClientTool, ClientToolCall, ToolOutput } from "./types";

/**
 * What the car can do. The list has a copy in CAR_TOOLS in agents/prompt/create_agents.py, and
 * tests/contract compares the two.
 */
export const CAR_TOOLS: ClientTool[] = tools;

export const WIPER_MODES = ["off", "slow", "fast"] as const;
export type WiperMode = (typeof WIPER_MODES)[number];

const MAX_HONKS = 5;

/** The car, as the tools see it. */
export interface CarControls {
  speedKmh(): number;
  setSpeed(kmh: number): void;
  setWipers(mode: WiperMode): void;
  /** False when the browser let no sound out. */
  honk(times: number): Promise<boolean>;
  /** Settles when the car has stopped. */
  park(): Promise<{ block: number; askedBlock?: number }>;
  signPhoto(): Promise<{ id?: string; message: string }>;
}

/** The arguments as an object, or undefined when they are something else. */
function parseArguments(value: unknown): Record<string, unknown> | undefined {
  let parsed = value;
  if (typeof value === "string" && value.trim() !== "") {
    try {
      parsed = JSON.parse(value);
    } catch {
      return undefined;
    }
  }
  if (parsed === undefined || parsed === null || parsed === "") return {};
  return typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : undefined;
}

export function isWhole(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}

/** Run one tool and return text for the model. A failure is text too, so the turn can go on. */
export async function runCarTool(call: ClientToolCall, car: CarControls): Promise<ToolOutput> {
  const args = parseArguments(call.arguments);
  if (!args) return { output: "Error: the arguments must be a JSON object." };
  try {
    return await run(call.name, args, car);
  } catch (e) {
    return { output: `Error: ${e instanceof Error && e.message ? e.message : "the car could not do that."}` };
  }
}

async function run(name: string, args: Record<string, unknown>, car: CarControls): Promise<ToolOutput> {
  switch (name) {
    case "set_speed": {
      const { kmh } = args;
      if (!isWhole(kmh, 0, TOP_KMH)) return { output: `Error: kmh must be a whole number from 0 to ${TOP_KMH}.` };
      const now = Math.round(car.speedKmh());
      car.setSpeed(kmh);
      if (kmh === now) return { output: `The car is already at ${kmh} km/h.` };
      return { output: `The car is ${kmh > now ? "accelerating" : "braking"} to ${kmh} km/h.` };
    }
    case "set_wipers": {
      const mode = WIPER_MODES.find((m) => m === args.mode);
      if (!mode) return { output: `Error: mode must be one of ${WIPER_MODES.join(", ")}.` };
      car.setWipers(mode);
      return { output: `The wipers are ${mode}.` };
    }
    case "honk": {
      const { times } = args;
      if (!isWhole(times, 1, MAX_HONKS)) return { output: `Error: times must be a whole number from 1 to ${MAX_HONKS}.` };
      const heard = await car.honk(times);
      return { output: heard ? `Honked ${times} time(s).` : "The browser blocked the sound. Click in the car first." };
    }
    case "park": {
      const { block, askedBlock } = await car.park();
      if (askedBlock === undefined) return { output: "Parked." };
      if (block === askedBlock) return { output: "Parked, in the block you asked about." };
      return {
        output: "Parked in another block. Another sign applies here: ask again before you leave the car.",
      };
    }
    case "get_sign_photo": {
      const { id, message } = await car.signPhoto();
      return id ? { output: message, attachments: [id] } : { output: message };
    }
    default:
      return { output: `Error: this car has no tool named '${name}'.` };
  }
}
