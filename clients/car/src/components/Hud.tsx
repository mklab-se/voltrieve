import { useEffect, useRef } from "react";
import type { HudEvent } from "../hud";
import type { Turn } from "../turns";
import type { SourceItem } from "../types";
import { Answer } from "./Answer";
import { CloseIcon } from "./icons";
import { ASSISTANT_NAME, RunaAvatar } from "./Runa";
import { SourceList } from "./SourceList";

interface Props {
  open: boolean;
  notice?: string;
  /** What the driver said, before there is a turn for it. */
  pending?: string;
  /** What the car does for Runa at the moment. */
  doing?: string;
  turn?: Turn;
  sourceError?: string;
  onEvent: (event: HudEvent) => void;
  onOpenSource: (source: SourceItem) => void;
}

/** The conversation, floating over the dashboard in large type. */
export function Hud({ open, notice, pending, doing, turn, sourceError, onEvent, onOpenSource }: Props) {
  const scroll = useRef<HTMLDivElement>(null);
  const shown = pending === undefined ? turn : undefined;
  const working = pending !== undefined || turn?.status === "streaming";
  const activity = doing ?? shown?.activity ?? (pending === undefined ? "Thinking" : "Looking at the sign");

  // Follow the answer as it comes, unless the driver has scrolled up.
  useEffect(() => {
    const el = scroll.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
  }, [shown?.answer]);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = 0;
  }, [shown?.id]);

  return (
    <section
      className="hud"
      data-open={open}
      inert={!open}
      aria-label={ASSISTANT_NAME}
      onPointerEnter={() => onEvent("pointerenter")}
      onPointerLeave={() => onEvent("pointerleave")}
      onFocus={() => onEvent("focus")}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) onEvent("blur");
      }}
    >
      <div className="hud-glass">
        <header>
          <RunaAvatar />
          <span>{ASSISTANT_NAME}</span>
          {notice && <span className="hud-notice">{notice}</span>}
          <button type="button" aria-label="Close" onClick={() => onEvent("escape")}>
            <CloseIcon />
          </button>
        </header>
        <div className="hud-scroll" data-scroll="" ref={scroll} tabIndex={0} role="group" aria-label="Answer">
          <p className="hud-said">{pending ?? shown?.userText}</p>
          {shown?.answer && <Answer text={shown.answer} sources={shown.sources} onOpenSource={onOpenSource} />}
          {working && !shown?.answer && !shown?.error && (
            <p className="thinking">
              <i />
              <i />
              <i />
              <span>{activity}</span>
            </p>
          )}
          {shown?.error && <p className="hud-error">{shown.error}</p>}
          {sourceError && <p className="hud-error">{sourceError}</p>}
          {shown?.sources && shown.sources.length > 0 && <SourceList sources={shown.sources} onOpen={onOpenSource} />}
        </div>
      </div>
    </section>
  );
}
