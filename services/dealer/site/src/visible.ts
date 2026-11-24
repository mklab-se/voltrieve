import { useSyncExternalStore } from "react";

function subscribe(changed: () => void): () => void {
  if (typeof document === "undefined") return () => {};
  document.addEventListener("visibilitychange", changed);
  return () => document.removeEventListener("visibilitychange", changed);
}
const read = () => typeof document === "undefined" || document.visibilityState !== "hidden";

/** Whether the tab can be seen. */
export function useVisible(): boolean {
  return useSyncExternalStore(subscribe, read);
}
