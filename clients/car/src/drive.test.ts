import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Upload, UploadResult } from "./attachments";
import { Drive } from "./drive";

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));
const photo = async (block: number) => new Blob([`block ${block}`]);
const uploads = (id = "att1") => vi.fn(async (): Promise<UploadResult> => ({ status: "ready", id }));
const drive = (upload: Upload = uploads()) => new Drive(async () => true, photo, upload);

function tick(d: Drive, seconds: number) {
  for (let i = 0; i < seconds * 60; i += 1) d.tick(1 / 60);
}

describe("Drive", () => {
  it("drives at the speed that is set", () => {
    const d = drive();
    d.setSpeed(50);
    tick(d, 10);
    expect(d.speedKmh()).toBe(50);
    expect(d.car.distanceM).toBeGreaterThan(100);
  });

  it("parks, and answers with the block when it stands still", async () => {
    const d = drive();
    d.askedBlock = 0;
    const done = vi.fn();
    void d.park().then(done);
    tick(d, 5);
    await settled();
    expect(done).not.toHaveBeenCalled();
    tick(d, 60);
    await settled();
    expect(done).toHaveBeenCalledWith({ block: 0, askedBlock: 0 });
  });

  it("answers at once when it is parked already", async () => {
    const d = drive();
    void d.park();
    tick(d, 60);
    expect(await d.park()).toEqual({ block: 0, askedBlock: undefined });
  });

  it("does not let a parking wait for ever when the driver goes on", async () => {
    const d = drive();
    const parking = d.park();
    tick(d, 2);
    d.setSpeed(80);
    await expect(parking).rejects.toThrow("The parking was given up, the car drives on.");
  });

  it("says so when the parking is given up to stop", async () => {
    const d = drive();
    const parking = d.park();
    tick(d, 2);
    d.setSpeed(0);
    await expect(parking).rejects.toThrow("The parking was given up, the car stops.");
  });

  it("sets the wipers and sounds the horn", async () => {
    const horn = vi.fn(async () => false);
    const d = new Drive(horn, photo, uploads());
    d.setWipers("fast");
    expect(d.wipers).toBe("fast");
    expect(await d.honk(3)).toBe(false);
    expect(horn).toHaveBeenCalledWith(3);
  });
});

describe("the photo of the sign", () => {
  it("is taken when the car passes it", async () => {
    const d = drive();
    d.setSpeed(30);
    tick(d, 2);
    expect(await d.signPhoto()).toEqual({ message: "No parking sign has been passed yet." });
    tick(d, 2);
    expect(await d.signPhoto()).toMatchObject({ id: "att1" }); // also right after it was passed
    expect(d.photos.last()!.block).toBe(0);
    expect(await d.photos.last()!.blob.text()).toBe("block 0");
    tick(d, 60);
    await settled();
    expect(d.photos.last()!.block).toBe(1);
  });

  it("makes its block the one that was asked about, and only when the photo is good", async () => {
    const rejecting = vi.fn(async (): Promise<UploadResult> => ({ status: "rejected", message: "blurred" }));
    for (const [upload, block] of [[rejecting, undefined], [uploads(), 0]] as const) {
      const d = drive(upload);
      d.setSpeed(50);
      tick(d, 5);
      await d.signPhoto();
      expect(d.askedBlock).toBe(block);
    }
    const d = drive(rejecting);
    d.setSpeed(50);
    tick(d, 5);
    expect(await d.signPhoto()).toEqual({ message: "blurred" });
  });

  it("is uploaded again on request", async () => {
    const upload = uploads();
    const d = drive(upload);
    d.setSpeed(50);
    tick(d, 5);
    await d.signPhoto();
    expect(await d.renewPhotos(["att1"])).toEqual({ ids: ["att1"] });
    expect(upload).toHaveBeenCalledTimes(2);
  });

  it("leaves the photo before it when the camera fails", async () => {
    const d = new Drive(async () => true, () => Promise.reject(new Error("no canvas")), uploads());
    d.setSpeed(50);
    tick(d, 5);
    expect(await d.signPhoto()).toEqual({ message: "No parking sign has been passed yet." });
  });
});

// A hidden tab gets no frames, so nothing ticks. The turn that waits must not hang.
describe("without frames", () => {
  beforeEach(() => void vi.useFakeTimers());
  afterEach(() => void vi.useRealTimers());

  it("finishes the parking in its bay", async () => {
    const d = drive();
    d.setSpeed(50);
    tick(d, 5);
    const parking = d.park();
    const { bayM } = d.car;
    await vi.advanceTimersByTimeAsync(5000);
    expect(await parking).toEqual({ block: 0, askedBlock: undefined });
    expect(d.car).toMatchObject({ mode: "parked", distanceM: bayM, speedKmh: 0 });
  });

  it("does not hurry a parking that gets its frames, however slow", async () => {
    const d = drive();
    const done = vi.fn();
    void d.park().then(done);
    for (let i = 0; i < 10; i += 1) {
      d.tick(0.1);
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(done).not.toHaveBeenCalled();
    expect(d.car.mode).toBe("parking");
  });

  it("finishes it at once when the tab is hidden", async () => {
    const d = drive();
    const parking = d.park();
    d.finishParking();
    expect(await parking).toMatchObject({ block: 0 });
    expect(d.car.mode).toBe("parked");
    d.finishParking(); // there is nothing to finish
    expect(d.car.mode).toBe("parked");
  });

  it("passes the signs on the way there", async () => {
    const d = drive();
    const parking = d.park(); // the first bay is behind the first sign
    d.finishParking();
    await parking;
    expect((await d.signPhoto()).id).toBe("att1");
  });

  it("lets go of a parking whose turn is over, and the car still parks", async () => {
    const d = drive();
    const parking = d.park();
    d.forgetParking();
    await expect(parking).rejects.toThrow("Nobody waits for the parking any more.");
    tick(d, 60);
    expect(d.car.mode).toBe("parked");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not wait for ever for a photo", async () => {
    const d = new Drive(async () => true, () => new Promise(() => {}), uploads());
    d.setSpeed(50);
    tick(d, 5);
    const photo = d.signPhoto();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await photo).toEqual({ message: "The photo of the sign is not ready yet. Try again." });
  });
});

describe("a camera that fails", () => {
  it("by throwing does not stop the car", async () => {
    const d = new Drive(
      async () => true,
      () => {
        throw new Error("no street");
      },
      uploads(),
    );
    d.setSpeed(50);
    expect(() => tick(d, 5)).not.toThrow();
    expect(await d.signPhoto()).toEqual({ message: "No parking sign has been passed yet." });
  });

  // An unhandled rejection makes vitest fail.
  it("while the photo before it is still taken is no unhandled rejection", async () => {
    const answers: (() => Promise<Blob>)[] = [
      () => new Promise((resolve) => setTimeout(() => resolve(new Blob(["late"])), 30)),
      () => Promise.reject(new Error("no canvas")),
    ];
    const d = new Drive(async () => true, () => answers.shift()!(), uploads());
    d.setSpeed(100);
    tick(d, 25);
    await settled();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(await d.photos.last()!.blob.text()).toBe("late");
  });
});
