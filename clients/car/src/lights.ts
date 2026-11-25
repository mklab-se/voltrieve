/** The lights of the car, when they flash. One flash takes 0.6 s, so it stays under three a second. */

export const FLASH_MS = 600;

export interface Lights {
  flash(times: number): void;
  stop(): void;
}

/** The page shows the flashes while it is told that they are on. More of them come after those going on. */
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
      until = Math.max(until, now) + times * FLASH_MS;
      clearTimeout(timer);
      timer = setTimeout(stop, until - now);
      onChange(true);
    },
    stop,
  };
}
