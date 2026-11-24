import { describe, expect, it } from "vitest";
import { Pedals } from "./pedals";

/** A car that does at once what it is told, for what the pedals tell it. */
function rig(speedKmh = 40) {
  const car = { speed: speedKmh, target: speedKmh, speedKmh: () => car.speed, setSpeed: (kmh: number) => void (car.target = kmh) };
  return { car, pedals: new Pedals(car) };
}

describe("the pedals", () => {
  it("accelerate and brake while they are held", () => {
    const { car, pedals } = rig();
    pedals.press("accelerate", 0);
    expect(car.target).toBe(150);
    pedals.press("brake", 100);
    expect(car.target).toBe(0);
  });

  it("keep the speed when they are let go", () => {
    const { car, pedals } = rig();
    pedals.press("accelerate", 0);
    car.speed = 63.4;
    pedals.release("accelerate", 2000);
    expect(car.target).toBe(63);
  });

  it("change the speed by 5 km/h on a short tap", () => {
    const { car, pedals } = rig(42);
    pedals.press("accelerate", 0);
    pedals.release("accelerate", 120);
    expect(car.target).toBe(45);
    pedals.press("brake", 1000);
    pedals.release("brake", 1100);
    expect(car.target).toBe(40);
  });

  it("add up several taps, although the car has not got there yet", () => {
    const { car, pedals } = rig(0);
    for (let i = 0; i < 4; i += 1) {
      pedals.press("accelerate", i * 200);
      pedals.release("accelerate", i * 200 + 80);
    }
    expect(car.target).toBe(20);
  });

  it("stay within what the car can do", () => {
    const { car, pedals } = rig(0);
    pedals.press("brake", 0);
    pedals.release("brake", 50);
    expect(car.target).toBe(0);
  });

  it("stop the car when the brake is held until it stands", () => {
    const { car, pedals } = rig(30);
    pedals.press("brake", 0);
    car.speed = 0;
    pedals.release("brake", 3000);
    expect(car.target).toBe(0);
  });

  it("ignore a pedal that is pressed already, or let go without being pressed", () => {
    const { car, pedals } = rig(30);
    pedals.release("accelerate", 0);
    expect(car.target).toBe(30);
    pedals.press("accelerate", 0);
    car.target = 90; // someone else has set the speed
    pedals.press("accelerate", 300); // the key repeats
    expect(car.target).toBe(90);
  });

  it("let go of everything at once", () => {
    const { car, pedals } = rig(30);
    pedals.press("accelerate", 0);
    car.speed = 50;
    pedals.releaseAll(5000);
    expect(car.target).toBe(50);
    pedals.releaseAll(6000);
    expect(car.target).toBe(50);
  });

  it("react to a click that comes alone, as assistive technology sends it", () => {
    const { car, pedals } = rig(40);
    pedals.click("accelerate", 0);
    expect(car.target).toBe(45);
  });

  it("do not count the click that follows a press twice", () => {
    const { car, pedals } = rig(40);
    pedals.press("accelerate", 0);
    pedals.click("accelerate", 10); // the Enter key clicks while it is down
    expect(car.target).toBe(150);
    pedals.release("accelerate", 80);
    pedals.click("accelerate", 85); // the pointer and the space key click when they are let go
    expect(car.target).toBe(45);
    pedals.click("accelerate", 800);
    expect(car.target).toBe(50);
  });
});
