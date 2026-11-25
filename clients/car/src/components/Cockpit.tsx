import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { drivesWith, typesInto } from "../ask";
import { CENTER_SCREEN, DRIVER_DISPLAY, fitPicture, hudHeight, WINDSCREEN, type Box } from "../layout";
import { hudNotice, type HudEvent, type HudState } from "../hud";
import type { Pedals } from "../pedals";
import { spoken } from "../spoken";
import type { ChatState } from "../turns";
import type { SourceItem } from "../types";
import type { Driving } from "../useDriving";
import type { Remote } from "../useRemote";
import { CenterScreen } from "./CenterScreen";
import { DriverDisplay } from "./DriverDisplay";
import { Hud } from "./Hud";

interface Props {
  canvas: RefObject<HTMLCanvasElement | null>;
  driving: Driving;
  pedals: Pedals;
  chat: ChatState;
  busy: boolean;
  pending?: string;
  doing?: string;
  hud: HudState;
  onHud: (event: HudEvent) => void;
  now: () => Date;
  onSetClock: (time: Date | undefined) => void;
  motorOn: boolean;
  onMotor: (on: boolean) => void;
  onSend: (text: string) => void;
  onPark: () => void;
  sourceError?: string;
  onOpenSource: (source: SourceItem) => void;
  remote: Remote;
}

const place = ({ left, top, width, height }: Box): CSSProperties => ({
  left: `${left}%`,
  top: `${top}%`,
  width: `${width}%`,
  height: `${height}%`,
});

/** The size of an element, as it changes. */
function useSize(element: RefObject<HTMLElement | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 1440, height: 900 });
  useLayoutEffect(() => {
    const el = element.current;
    if (!el) return;
    const measure = () => {
      if (el.clientWidth > 0 && el.clientHeight > 0) setSize({ width: el.clientWidth, height: el.clientHeight });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [element]);
  return size;
}

/** Typing starts a message, the up and down keys drive, and Escape puts the HUD back. */
export function useKeys(field: RefObject<HTMLInputElement | null>, pedals: Pedals, onHud: (event: HudEvent) => void): void {
  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      const focused = document.activeElement;
      if (e.key === "Escape") {
        if (focused === field.current) field.current?.blur();
        onHud("escape");
        return;
      }
      const pedal = drivesWith(e, focused);
      if (pedal) {
        e.preventDefault(); // nothing scrolls
        pedals.press(pedal, performance.now());
      } else if (typesInto(e, focused?.tagName)) {
        field.current?.focus(); // the key itself then goes into the field
      }
    };
    const onUp = (e: KeyboardEvent) => {
      if (e.key === "ArrowUp") pedals.release("accelerate", performance.now());
      if (e.key === "ArrowDown") pedals.release("brake", performance.now());
    };
    const onBlur = () => pedals.releaseAll(performance.now());
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
    };
  }, [field, pedals, onHud]);
}

export function Cockpit(props: Props) {
  const { canvas, driving, pedals, chat, pending, hud, onHud } = props;
  const frame = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const size = useSize(frame);
  const box = fitPicture(size.width, size.height);
  useKeys(field, pedals, onHud);

  const picture = { ...box, "--u": `${box.width / 100}px`, "--hud-max": `${hudHeight(box)}px` } as CSSProperties;
  const notice = hudNotice(hud);
  return (
    <main className="cockpit" ref={frame}>
      <div className="picture" style={picture} data-street={driving.street}>
        <canvas ref={canvas} className="street" style={place(WINDSCREEN)} role="img" aria-label="The street ahead" />
        {props.remote.flashing && <div className="headlights flashing" style={place(WINDSCREEN)} aria-hidden="true" />}
        <img className="cockpit-picture" src={`${import.meta.env.BASE_URL}cockpit.webp`} alt="" draggable={false} />
        {driving.street === "failed" && <p className="no-street">The street could not be drawn in this browser. The car drives all the same.</p>}
        <DriverDisplay gauges={driving.gauges} flashing={props.remote.flashing} style={place(DRIVER_DISPLAY)} />
        <CenterScreen {...props} style={place(CENTER_SCREEN)} state={chat} lifted={hud.open} gauges={driving.gauges} sign={driving.sign} field={field} />
        <Hud
          open={hud.open}
          notice={notice}
          pending={pending}
          doing={props.doing}
          turn={chat.turns.at(-1)}
          sourceError={props.sourceError}
          onEvent={onHud}
          onOpenSource={props.onOpenSource}
        />
      </div>
      <p className="visually-hidden" aria-live="polite">
        {spoken(chat, props.doing)}
      </p>
      <p className="visually-hidden" aria-live="polite">
        {notice}
      </p>
      <p className="visually-hidden" role="alert">
        {chat.turns.at(-1)?.error ?? props.sourceError}
      </p>
    </main>
  );
}
