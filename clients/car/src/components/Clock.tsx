import { useEffect, useRef, useState } from "react";

interface Props {
  /** What the clock of the car shows now. */
  now: () => Date;
  /** Undefined sets it to the real time. */
  onSet: (time: Date | undefined) => void;
}

const EARLIEST = "2000-01-01T00:00";
const LATEST = "2099-12-31T23:59";

/** The time in the field, or undefined while it is not a whole one: a year is 0002 when its first digit is typed. */
export function readClock(value: string): Date | undefined {
  const time = new Date(value);
  const year = time.getFullYear();
  return year >= 2000 && year <= 2099 ? time : undefined;
}

const two = (n: number) => String(n).padStart(2, "0");
const timeText = (d: Date) => `${two(d.getHours())}:${two(d.getMinutes())}`;
const dayText = (d: Date) => d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const fieldText = (d: Date) => `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${timeText(d)}`;

/** The clock of the car. A tap opens the setter. */
export function Clock({ now, onSet }: Props) {
  const [, setTick] = useState(0);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  const time = now();
  return (
    <div
      className="clock"
      ref={box}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        setOpen(false);
      }}
    >
      <button type="button" className="clock-face" aria-expanded={open} aria-label={`${timeText(time)}, ${dayText(time)}. Set the clock`} onClick={() => setOpen(!open)}>
        <strong>{timeText(time)}</strong>
        <span>{dayText(time)}</span>
      </button>
      {open && (
        <div className="clock-setter" role="dialog" aria-label="Set the clock">
          <label>
            Day and time
            <input
              type="datetime-local"
              autoFocus
              min={EARLIEST}
              max={LATEST}
              defaultValue={fieldText(time)}
              onChange={(e) => {
                const set = readClock(e.target.value);
                if (set) onSet(set);
              }}
              onKeyDown={(e) => e.key === "Enter" && setOpen(false)}
            />
          </label>
          <div>
            <button type="button" className="ghost" onClick={() => (onSet(undefined), setOpen(false))}>
              Real time
            </button>
            <button type="button" className="primary" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
