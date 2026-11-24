import type { Pedal, Pedals } from "../pedals";
import { ArrowIcon } from "./icons";

const LABELS: Record<Pedal, string> = { accelerate: "Accelerate", brake: "Brake" };
const KEYS: Record<Pedal, string> = { brake: "ArrowDown", accelerate: "ArrowUp" };

/** The two pedals on the screen. The up and down keys do the same. */
export function PedalButtons({ pedals }: { pedals: Pedals }) {
  const now = () => performance.now();
  return (
    <div className="pedals">
      {(["accelerate", "brake"] as const).map((pedal) => (
        <button
          key={pedal}
          type="button"
          className={`pedal ${pedal}`}
          aria-label={LABELS[pedal]}
          aria-keyshortcuts={KEYS[pedal]}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.currentTarget.setPointerCapture(e.pointerId);
            pedals.press(pedal, now());
          }}
          onPointerUp={() => pedals.release(pedal, now())}
          onPointerCancel={() => pedals.release(pedal, now())}
          onKeyDown={(e) => {
            if (e.key === " " || e.key === "Enter") pedals.press(pedal, now());
          }}
          onKeyUp={(e) => {
            if (e.key === " " || e.key === "Enter") pedals.release(pedal, now());
          }}
          onClick={() => pedals.click(pedal, now())}
          onBlur={() => pedals.releaseAll(now())}
        >
          <ArrowIcon up={pedal === "accelerate"} />
          <span aria-hidden="true">{pedal === "brake" ? "Brake" : "Go"}</span>
        </button>
      ))}
    </div>
  );
}
