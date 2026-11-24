// Copied from clients/web; keep the two alike.
import type { ChatState } from "../turns";
import { BACKENDS, type Backend } from "../types";
import { Recorder } from "./Recorder";

const STORAGE_KEY = "under-the-hood";

/** Off unless the user turned it on in this browser. */
export function readUnderTheHood(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

export function rememberUnderTheHood(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    /* storage may be unavailable */
  }
}

const HINT: Record<Backend, string> = {
  loop: "Own loop in the Web API",
  prompt: "Prompt agent in Foundry",
  hosted: "Hosted agent in Foundry",
};

interface Props {
  state: ChatState;
  backend: Backend;
  onBackend: (backend: Backend) => void;
  busy: boolean;
}

/** Everything that shows how an answer was made. Not part of the car until it is asked for. */
export function UnderTheHood({ state, backend, onBackend, busy }: Props) {
  return (
    <aside className="hood" aria-label="Under the hood">
      <div className="hood-bar">
        <span>Backend</span>
        <div className="seg" role="radiogroup" aria-label="Agent backend">
          {BACKENDS.map((b) => (
            <button
              key={b}
              type="button"
              role="radio"
              aria-checked={b === backend}
              className={b === backend ? "on" : ""}
              title={HINT[b]}
              disabled={busy}
              onClick={() => onBackend(b)}
            >
              {b}
            </button>
          ))}
        </div>
      </div>
      <Recorder state={state} />
    </aside>
  );
}
