import { useEffect, useRef, useState, type RefObject } from "react";
import type { Drive } from "./drive";
import { readGauges, type Gauges } from "./gauges";
import type { Motor } from "./motor";
import type { StreetScene } from "./scene/Scene";

export interface LastPhoto {
  block: number;
  url: string;
}

export interface Driving {
  gauges: Gauges;
  sign?: LastPhoto;
  street: "loading" | "shown" | "failed";
}

const same = <T extends object>(a: T, b: T) => Object.keys({ ...a, ...b }).every((k) => a[k as keyof T] === b[k as keyof T]);

/** Moves the car and draws the street, once for every frame, and tells React only what has changed. */
export function useDriving(
  drive: Drive,
  canvas: RefObject<HTMLCanvasElement | null>,
  scene: RefObject<StreetScene | undefined>,
  motor: Motor,
): Driving {
  const [gauges, setGauges] = useState(() => readGauges(drive.car, drive.wipers));
  const [sign, setSign] = useState<LastPhoto>();
  const [street, setStreet] = useState<Driving["street"]>("loading");
  const latest = useRef({ gauges, sign });

  useEffect(() => {
    let stopped = false;
    // three.js is large, so it comes after the first paint and never with the sign-in page.
    void (async () => {
      try {
        const { StreetScene } = await import("./scene/Scene");
        if (stopped || !canvas.current) return;
        scene.current = new StreetScene(canvas.current);
      } catch {
        if (!stopped) setStreet("failed");
      }
    })();

    let frame = 0;
    let frames = 0;
    let before = performance.now();
    const onFrame = (now: number) => {
      frame = requestAnimationFrame(onFrame);
      drive.tick((now - before) / 1000);
      before = now;
      motor.setSpeed(drive.car.speedKmh);
      if (scene.current) {
        try {
          scene.current.render(drive.car, drive.wipers);
          frames += 1;
          if (frames === 2) setStreet("shown");
        } catch {
          scene.current = undefined;
          setStreet("failed");
        }
      }
      const next = readGauges(drive.car, drive.wipers);
      if (!same(next, latest.current.gauges)) setGauges((latest.current.gauges = next));
      const photo = drive.photos.last();
      if (photo && photo.url !== latest.current.sign?.url) {
        setSign((latest.current.sign = { block: photo.block, url: photo.url }));
      }
    };
    frame = requestAnimationFrame(onFrame);
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      scene.current?.dispose();
      scene.current = undefined;
    };
  }, [drive, canvas, scene, motor]);

  return { gauges, sign, street };
}
