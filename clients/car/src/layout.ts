/** Where things are on the cockpit picture, and how the picture fills the window. Pure. */

export const PICTURE = { width: 1536, height: 1024 } as const;

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** In percent of the picture. */
export const CENTER_SCREEN: Box = { left: 36.65, top: 43.46, width: 29.1, height: 25.98 };
export const DRIVER_DISPLAY: Box = { left: 13.3, top: 52.9, width: 18.8, height: 10.3 };
/** The part that the street is drawn behind: the windscreen and the mirror, with a little to spare. */
export const WINDSCREEN: Box = { left: 1.5, top: 1.6, width: 97, height: 48.4 };
/** The glass of the rear view mirror. */
export const MIRROR: Box = { left: 38.28, top: 8.6, width: 20.97, height: 7.1 };

/** Everything the driver uses is above this line. Below it are the footwell and the tunnel. */
const NEEDED = 0.72;

/** The picture fills the width. Wide windows lose its bottom, and very wide ones also its roof. */
export function fitPicture(width: number, height: number): Box {
  const scale = Math.max(width / PICTURE.width, height / PICTURE.height);
  const full = { width: PICTURE.width * scale, height: PICTURE.height * scale };
  return { left: (width - full.width) / 2, top: Math.min(height - NEEDED * full.height, 0), ...full };
}
