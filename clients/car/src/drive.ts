import { SignPhotos, type Renewed, type Upload } from "./attachments";
import type { Horn } from "./horn";
import { newCar, setSpeed, startParking, step, type Car } from "./sim/car";
import { blockOf, passedSign } from "./sim/street";
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
  readonly photos = new SignPhotos();
  private parking?: Parking;
  private taking: Promise<void> = Promise.resolve();

  constructor(
    private readonly horn: Horn,
    private readonly takePhoto: (block: number) => Promise<Blob>,
    private readonly upload: Upload,
  ) {}

  /** Call it for every frame, with the seconds since the last one. */
  tick(dtSeconds: number): void {
    const before = this.car.distanceM;
    this.car = step(this.car, dtSeconds);
    const block = passedSign(before, this.car.distanceM);
    if (block !== undefined) this.photograph(block);
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
    await this.taking; // the car may just have passed the sign
    const result = await this.photos.idForLast(this.upload);
    if (result.status !== "ready") return { message: result.message };
    this.askedBlock = result.block;
    return { id: result.id, message: "The photo is attached." };
  }

  /** New ids for photos that the API has lost. */
  renewPhotos(ids: string[]): Promise<Renewed> {
    return this.photos.renew(ids, this.upload);
  }

  private photograph(block: number): void {
    const photo = this.takePhoto(block);
    const before = this.taking;
    this.taking = (async () => {
      await before;
      try {
        this.photos.add(block, await photo);
      } catch {
        // No photo of this sign. The one before it stays.
      }
    })();
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
