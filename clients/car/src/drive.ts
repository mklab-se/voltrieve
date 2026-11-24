import { SignPhotos, type Renewed, type Upload } from "./attachments";
import type { Horn } from "./horn";
import { lastSignBlock } from "./gauges";
import { newCar, setSpeed, startParking, step, type Car } from "./sim/car";
import { blockOf, passedSign } from "./sim/street";
import type { CarControls, WiperMode } from "./tools";

/** Without frames for this long, the tab is hidden, and the car does not move by itself. */
const NO_FRAMES_MS = 3000;
/** The turn waits no longer than this for the car to stand. */
const PARK_MS = 60_000;
/** A photo that takes longer than this is given up, so that the later ones come. */
const PHOTO_MS = 6000;
/** The turn waits no longer than this for the photos to be taken. */
const PHOTOS_MS = 8000;
const STEP_S = 0.1;
const OLD_CHAT = { message: "A new chat has begun." };

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
  private watch?: ReturnType<typeof setInterval>;
  private lastFrameMs = Date.now();
  /** Which chat it is: what an old one still waits for says nothing about the new one. */
  private chat = 0;

  constructor(
    private readonly horn: Horn,
    private readonly takePhoto: (block: number) => Promise<Blob>,
    private readonly upload: Upload,
  ) {}

  /** Call it for every frame, with the seconds since the last one. */
  tick(dtSeconds: number): void {
    this.lastFrameMs = Date.now();
    this.move(dtSeconds);
  }

  private move(dtSeconds: number): void {
    const before = this.car.distanceM;
    this.car = step(this.car, dtSeconds);
    const block = passedSign(before, this.car.distanceM);
    if (block !== undefined) this.photograph(block);
    if (this.car.mode === "parked") this.settle((parking) => parking.stopped());
  }

  speedKmh(): number {
    return this.car.speedKmh;
  }

  setSpeed(kmh: number): void {
    this.car = setSpeed(this.car, kmh);
    if (this.car.mode === "parking" || this.car.mode === "parked") return;
    const what = kmh > 0 ? "drives on" : "stops";
    this.settle((parking) => parking.givenUp(new Error(`The parking was given up, the car ${what}.`)));
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
      this.watchParking();
      await this.parking.promise;
    }
    return { block: blockOf(this.car.bayM ?? this.car.distanceM), askedBlock: this.askedBlock };
  }

  /** The rest of the way into the bay at once, for a tab that is hidden and gets no frames. */
  finishParking(): void {
    while (this.car.mode === "parking") this.move(STEP_S);
  }

  /** Nobody waits for the car to stand any more. It parks all the same. */
  forgetParking(): void {
    this.settle((parking) => parking.givenUp(new Error("Nobody waits for the parking any more.")));
  }

  async signPhoto(): Promise<{ id?: string; message: string }> {
    // The car may just have passed the sign. The photo before it is of another one, so it is no answer.
    const { chat } = this;
    const taken = await inTime(this.taking.then(() => true), PHOTOS_MS, false);
    if (chat !== this.chat) return OLD_CHAT;
    if (!taken) return { message: "The photo of the sign is not ready yet. Try again." };
    const here = lastSignBlock(this.car.distanceM);
    if (here !== undefined && !this.photos.last()) return { message: "The photo of the last sign could not be taken." };
    const result = await this.photos.idForLast(this.upload);
    if (chat !== this.chat) return OLD_CHAT;
    if (result.status !== "ready") return { message: result.message };
    this.askedBlock = result.block;
    const shows = `The photo is attached. It shows the sign of block ${result.block + 1}.`;
    const now = lastSignBlock(this.car.distanceM);
    if (now === undefined || now === result.block) return { id: result.id, message: shows };
    return { id: result.id, message: `${shows} The car is in block ${now + 1} now, where another sign applies.` };
  }

  /** A new chat: it has asked about no block, and waits for nothing. */
  startOver(): void {
    this.chat += 1;
    this.askedBlock = undefined;
    this.forgetParking();
  }

  /** New ids for photos that the API has lost. */
  renewPhotos(ids: string[]): Promise<Renewed> {
    return this.photos.renew(ids, this.upload);
  }

  private settle(how: (parking: Parking) => void): void {
    const { parking } = this;
    this.parking = undefined;
    clearInterval(this.watch);
    this.watch = undefined;
    if (parking) how(parking);
  }

  private watchParking(): void {
    if (this.watch !== undefined) return;
    const start = Date.now();
    this.lastFrameMs = start;
    this.watch = setInterval(() => {
      const now = Date.now();
      if (now - this.lastFrameMs > NO_FRAMES_MS || now - start > PARK_MS) this.finishParking();
    }, 1000);
  }

  private photograph(block: number): void {
    // Its end is awaited at once: a photo that fails early must not count as an error nobody saw.
    const photo = this.tryPhoto(block);
    const before = this.taking;
    this.taking = (async () => {
      await before;
      const blob = await photo;
      if (blob) this.photos.add(block, blob); // else the photo before it stays
    })();
  }

  private async tryPhoto(block: number): Promise<Blob | undefined> {
    try {
      return await inTime(this.takePhoto(block), PHOTO_MS, undefined);
    } catch {
      return undefined;
    }
  }
}

/** The result, or the other one when it is not there in this time. */
async function inTime<T, L>(work: Promise<T>, ms: number, otherwise: L): Promise<T | L> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<L>((resolve) => {
    timer = setTimeout(resolve, ms, otherwise);
  });
  try {
    return await Promise.race([work, late]);
  } finally {
    clearTimeout(timer);
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
