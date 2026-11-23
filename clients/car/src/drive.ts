import type { Horn } from "./horn";
import { newCar, setSpeed, startParking, step, type Car } from "./sim/car";
import { blockOf } from "./sim/street";
import type { CarControls, WiperMode } from "./tools";

interface Parking {
  promise: Promise<void>;
  stopped: () => void;
  givenUp: (why: Error) => void;
}

/** The one car of the app: the simulation over time, and the controls that the tools and pedals use. */
export class Drive implements CarControls {
  car: Car = newCar();
  wipers: WiperMode = "off";
  /** The block the driver last asked about. */
  askedBlock?: number;
  private parking?: Parking;

  constructor(private readonly horn: Horn) {}

  /** Call it for every frame, with the seconds since the last one. */
  tick(dtSeconds: number): void {
    this.car = step(this.car, dtSeconds);
    if (this.car.mode !== "parked") return;
    this.parking?.stopped();
    this.parking = undefined;
  }

  speedKmh(): number {
    return this.car.speedKmh;
  }

  setSpeed(kmh: number): void {
    this.car = setSpeed(this.car, kmh);
    if (this.car.mode === "parking" || this.car.mode === "parked") return;
    const what = kmh > 0 ? "drives on" : "stops";
    this.parking?.givenUp(new Error(`The parking was given up, the car ${what}.`));
    this.parking = undefined;
  }

  setWipers(mode: WiperMode): void {
    this.wipers = mode;
  }

  honk(times: number): Promise<boolean> {
    return this.horn(times);
  }

  async park(): Promise<{ block: number; askedBlock?: number }> {
    this.car = startParking(this.car);
    if (this.car.mode !== "parked") {
      this.parking ??= newParking();
      await this.parking.promise;
    }
    return { block: blockOf(this.car.bayM ?? this.car.distanceM), askedBlock: this.askedBlock };
  }

  async signPhoto(): Promise<{ id?: string; message: string }> {
    return { message: "No parking sign has been passed yet." };
  }
}

function newParking(): Parking {
  let stopped = () => {};
  let givenUp = (_why: Error) => {};
  const promise = new Promise<void>((resolve, reject) => {
    stopped = resolve;
    givenUp = reject;
  });
  return { promise, stopped, givenUp };
}
