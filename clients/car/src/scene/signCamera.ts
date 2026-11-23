/** The camera that takes a close-up of a parking sign. */
import { PerspectiveCamera, Vector3 } from "three";

export const PHOTO_PX = 1024;
/** From here on the photo is blurred, and it says so to the driver. */
const SHARP_KMH = 70;
const TOP_KMH = 150;
const [LEAST_PX, MOST_PX] = [8, 24];

/** How much a photo at this speed is blurred, in pixels. */
export function blurPx(speedKmh: number): number {
  if (speedKmh <= SHARP_KMH) return 0;
  const part = Math.min((speedKmh - SHARP_KMH) / (TOP_KMH - SHARP_KMH), 1);
  return LEAST_PX + (MOST_PX - LEAST_PX) * part;
}

/** How much of the photo the sign takes, at most, across or down. */
const FILL = 0.82;
/** A sign with only a disc gets more of the street around it. */
const LEAST_VIEW_M = 1;
const FOV = 30;
const FROM_BELOW_M = 0.3;

export interface Board {
  center: Vector3;
  /** The way it faces, flat on the ground. */
  facing: Vector3;
  width: number;
  height: number;
}

/** How many meters of the street the photo shows, across and down. */
export function photoView(board: { width: number; height: number }): number {
  return Math.max(Math.max(board.width, board.height) / FILL, LEAST_VIEW_M);
}

/** A camera in front of the sign, a little below its middle. */
export function signCamera(board: Board): PerspectiveCamera {
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 1200);
  const away = photoView(board) / 2 / Math.tan((FOV * Math.PI) / 360);
  camera.position.copy(board.center).addScaledVector(board.facing, away);
  camera.position.y -= FROM_BELOW_M;
  camera.lookAt(board.center);
  camera.updateMatrixWorld();
  return camera;
}

/** The average of copies that are moved along a line. It works in browsers without canvas filters too. */
function smear(from: HTMLCanvasElement, dx: number, dy: number): HTMLCanvasElement {
  const to = document.createElement("canvas");
  to.width = from.width;
  to.height = from.height;
  const ctx = to.getContext("2d")!;
  const steps = Math.ceil(Math.max(Math.abs(dx), Math.abs(dy)) * 2) + 1;
  for (let i = 0; i < steps; i += 1) {
    const at = steps === 1 ? 0 : i / (steps - 1) - 0.5;
    ctx.globalAlpha = 1 / (i + 1);
    // Larger than the picture, so that the edges have something to show.
    const pad = Math.max(Math.abs(dx), Math.abs(dy));
    ctx.drawImage(from, at * 2 * dx - pad, at * 2 * dy - pad, to.width + 2 * pad, to.height + 2 * pad);
  }
  return to;
}

/** A moving car smears most along its way, and shakes a little the other way. */
export function motionBlur(photo: HTMLCanvasElement, px: number): HTMLCanvasElement {
  return px <= 0 ? photo : smear(smear(photo, px, 0), px * 0.15, px * 0.6);
}

export function toJpeg(photo: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) =>
    photo.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The photo could not be taken."))), "image/jpeg", 0.85),
  );
}
