import { useCallback, useInsertionEffect, useRef } from "react";

/** A function that stays the same one and always does what the last one given does. */
export function useStable<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const latest = useRef(fn);
  useInsertionEffect(() => {
    latest.current = fn;
  });
  return useCallback((...args: A) => latest.current(...args), []);
}
