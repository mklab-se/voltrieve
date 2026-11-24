import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { day, money } from "../format";
import { SIGNATURE_BOX, signaturePath } from "../signature";
import type { Order } from "../state";

// In (0.6 s), the pen draws (2 s), the stamp (0.4 s), a rest (1.8 s), and it flies to its row (0.7 s).
// The times of the first three are in styles.css too.
const FLY_AT_MS = 4800;
export const SIGNING_MS = 5500;
/** With reduced motion: the finished paper. */
export const STILL_MS = 3000;

type Rect = Pick<DOMRect, "left" | "top" | "width" | "height">;
interface Flight {
  x: number;
  y: number;
  scale: number;
}

/** How the paper gets to its row. Without a row it shrinks where it is. */
export function flight(paper: Rect, row: Rect | undefined): Flight {
  if (!row || !paper.height) return { x: 0, y: 0, scale: 0.2 };
  return {
    x: row.left + row.width / 2 - paper.left - paper.width / 2,
    y: row.top + row.height / 2 - paper.top - paper.height / 2,
    scale: row.height / paper.height,
  };
}

function rowOf(number: string): Element | undefined {
  if (typeof document === "undefined") return undefined;
  return [...document.querySelectorAll("[data-order]")].find((e) => e.getAttribute("data-order") === number);
}

interface Props {
  /** The caller gives it the order's number as key: one dialog for each order. */
  order: Order;
  model: string;
  /** The page is not dimmed yet. */
  first: boolean;
  /** How many come after this one. */
  waiting: number;
  onDone: (number: string) => void;
}

/** An order that arrived, as a paper that is signed, stamped and put in the list. */
export function Signing({ order, model, first, waiting, onDone }: Props) {
  const { number } = order;
  const dialog = useRef<HTMLDialogElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const [still] = useState(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [gone, setGone] = useState<Flight>();
  // The dim comes in with the first paper only, whatever is true later.
  const [dim] = useState(first);

  useLayoutEffect(() => {
    const d = dialog.current;
    d?.showModal();
    const timers = [setTimeout(() => onDone(number), still ? STILL_MS : SIGNING_MS)];
    if (!still) {
      const fly = () => {
        const row = rowOf(number);
        row?.scrollIntoView({ block: "nearest" });
        if (paper.current) setGone(flight(paper.current.getBoundingClientRect(), row?.getBoundingClientRect()));
      };
      timers.push(setTimeout(fly, FLY_AT_MS));
    }
    return () => {
      timers.forEach(clearTimeout);
      d?.close(); // and the focus goes back
    };
  }, [number, still, onDone]);

  const skip = () => onDone(number);
  const signature = signaturePath(order.name);
  const long = order.address.length + order.name.length > 90;
  return (
    <dialog
      className={`signing${still ? " still" : ""}${dim ? " first" : ""}${gone ? " gone" : ""}${waiting === 0 ? " last" : ""}`}
      ref={dialog}
      aria-label={`Purchase order ${number}, ${model}, for ${order.name}`}
      // The second click of a double click, or a key that is held, would skip the next one too.
      onClick={(e) => e.detail < 2 && skip()}
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        e.preventDefault();
        if (!e.repeat) skip();
      }}
      onCancel={(e) => {
        e.preventDefault();
        skip();
      }}
      // Also sent, late, by the close above, when it is open again or gone.
      onClose={() => dialog.current?.open === false && skip()}
    >
      <div
        className="paper"
        ref={paper}
        style={gone && ({ "--x": `${gone.x}px`, "--y": `${gone.y}px`, "--scale": gone.scale } as CSSProperties)}
      >
        <div className="paper-head">
          <span className="paper-brand">
            <svg viewBox="0 0 32 32" aria-hidden="true">
              <path d="M18 3 7 18h7l-1 11 11-15h-7z" fill="currentColor" />
            </svg>
            Voltrieve
          </span>
          <span className="paper-number">{number}</span>
        </div>
        <h2>Purchase order</h2>
        <dl className={long ? "long" : undefined}>
          <div>
            <dt>Car</dt>
            <dd className="paper-car">
              <span>{`Voltrieve ${model}`}</span> <span className="paper-price">{money(order.price_sek)}</span>
            </dd>
          </div>
          <div>
            <dt>Buyer</dt>
            <dd>
              <bdi>{order.name}</bdi>
            </dd>
          </div>
          <div>
            <dt>Address</dt>
            <dd>
              <bdi>{order.address}</bdi>
            </dd>
          </div>
          <div>
            <dt>Date</dt>
            <dd>{day(order.created)}</dd>
          </div>
        </dl>
        <div className="paper-sign">
          <svg className="signature" viewBox={`0 0 ${SIGNATURE_BOX.width} ${SIGNATURE_BOX.height}`} aria-hidden="true">
            {/* Twice, a little apart: the line is broader one way, as from a nib. */}
            <path className="ink" d={signature} pathLength={1} />
            <path className="ink" d={signature} pathLength={1} transform="translate(1.3 -1)" />
            {!still && (
              <g className="pen" style={{ offsetPath: `path("${signature}")` }}>
                <Pen />
              </g>
            )}
          </svg>
          <p className="paper-line">
            <bdi>{order.name}</bdi>
          </p>
          <div className="stamp" aria-hidden="true">
            <span>Order received</span>
            <strong>{number}</strong>
            <span>Voltrieve</span>
          </div>
        </div>
        <p className="paper-note">Demo order, not a real purchase</p>
      </div>
      <p className="skip">
        {waiting > 0 && `${waiting} more ${waiting === 1 ? "order" : "orders"}. `}
        Click or press Escape to skip.
      </p>
    </dialog>
  );
}

/** A fountain pen. Its tip is at 0, 0. */
function Pen() {
  return (
    <g className="pen-hold">
      <defs>
        <linearGradient id="barrel" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="#06251c" />
          <stop offset="0.3" stopColor="#2f7d63" />
          <stop offset="0.5" stopColor="#0f4a38" />
          <stop offset="1" stopColor="#041510" />
        </linearGradient>
        <linearGradient id="metal" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0" stopColor="#8a6a1f" />
          <stop offset="0.35" stopColor="#fff0bd" />
          <stop offset="0.65" stopColor="#d2a945" />
          <stop offset="1" stopColor="#6d5114" />
        </linearGradient>
      </defs>
      <path className="pen-shadow" d="M0 0 L9 -34 L12 -300 Q0 -312 -12 -300 L-9 -34 Z" />
      <g className="pen-body">
        <path d="M0 0 C5 -10 8 -22 8 -38 L-8 -38 C-8 -22 -5 -10 0 0 Z" fill="url(#metal)" />
        <path d="M0 -2 V-24" stroke="#4d3a10" strokeWidth="1" />
        <circle cx="0" cy="-25" r="1.8" fill="#4d3a10" />
        <path d="M-8 -38 L8 -38 L10.5 -74 L-10.5 -74 Z" fill="url(#barrel)" />
        <rect x="-11.5" y="-80" width="23" height="7" rx="2" fill="url(#metal)" />
        <path d="M-12 -80 L12 -80 C14 -150 14 -220 11 -282 Q0 -296 -11 -282 C-14 -220 -14 -150 -12 -80 Z" fill="url(#barrel)" />
        <rect x="-13.2" y="-176" width="26.4" height="5" rx="1.5" fill="url(#metal)" />
      </g>
    </g>
  );
}
