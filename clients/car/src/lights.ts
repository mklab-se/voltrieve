/** The lights of the car, when they flash. One flash takes 0.6 s, so it stays under three a second. */

export const FLASH_MS = 600;
/** Those that are still to come, the one going on included. */
export const MOST_FLASHES = 5;

export interface Lights {
  flash(times: number): void;
  stop(): void;
}

/** The page shows the flashes while it is told that they are on. More of them come after those going on, up to a limit. */
export function createLights(onChange: (on: boolean) => void): Lights {
  let until = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => {
    clearTimeout(timer);
    until = 0;
    onChange(false);
  };
  return {
    flash(times) {
      const now = Date.now();
      const start = Math.max(until, now);
      const left = Math.ceil((start - now) / FLASH_MS);
      until = start + Math.min(times, MOST_FLASHES - left) * FLASH_MS;
      clearTimeout(timer);
      timer = setTimeout(stop, until - now);
      onChange(true);
    },
    stop,
  };
}
