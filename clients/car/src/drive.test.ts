import { describe, expect, it, vi } from "vitest";
import { Drive } from "./drive";

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));
const drive = () => new Drive(async () => true);

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
    const d = new Drive(horn);
    d.setWipers("fast");
    expect(d.wipers).toBe("fast");
    expect(await d.honk(3)).toBe(false);
    expect(horn).toHaveBeenCalledWith(3);
  });
});
