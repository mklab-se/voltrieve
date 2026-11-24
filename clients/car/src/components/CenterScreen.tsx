import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type RefObject } from "react";
import type { Gauges } from "../gauges";
import type { Pedals } from "../pedals";
import type { ChatState } from "../turns";
import type { SourceItem } from "../types";
import { Answer } from "./Answer";
import { Clock } from "./Clock";
import { SendIcon, SpeakerIcon } from "./icons";
import { PedalButtons } from "./Pedals";
import { ASSISTANT_NAME, RunaAvatar } from "./Runa";

const STARTERS = ["Set the speed to 50", "Wipers on"];

export interface LastSign {
  block: number;
  url: string;
}

interface Props {
  style: CSSProperties;
  state: ChatState;
  busy: boolean;
  /** The HUD shows the conversation now. */
  lifted: boolean;
  gauges: Gauges;
  sign?: LastSign;
  pedals: Pedals;
  field: RefObject<HTMLInputElement | null>;
  now: () => Date;
  onSetClock: (time: Date | undefined) => void;
  motorOn: boolean;
  onMotor: (on: boolean) => void;
  onSend: (text: string) => void;
  onPark: () => void;
  onOpenSource: (source: SourceItem) => void;
}

function Messages({ state, lifted, busy, onSend, onOpenSource }: Pick<Props, "state" | "lifted" | "busy" | "onSend" | "onOpenSource">) {
  const list = useRef<HTMLDivElement>(null);
  const last = state.turns.at(-1);
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [state.turns.length, last?.answer, last?.error]);

  if (!last) {
    return (
      <div className="cs-messages empty">
        <p>
          I am {ASSISTANT_NAME}, an AI assistant. Ask about the signs, or tell the car what to do.
        </p>
        <div className="cs-starters">
          {STARTERS.map((s) => (
            <button key={s} type="button" onClick={() => onSend(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="cs-messages" ref={list} data-lifted={lifted} tabIndex={0} role="log" aria-live="off" aria-label="Conversation">
      {state.turns.map((t) => (
        <div key={t.id}>
          <p className="cs-said">{t.userText}</p>
          {t.answer && <Answer text={t.answer} sources={t.sources} onOpenSource={onOpenSource} />}
          {t.error && <p className="cs-error">{t.error}</p>}
          {!t.answer && !t.error && t.status === "streaming" && (
            <p className="thinking">
              <i />
              <i />
              <i />
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function SignCard({ sign, gauges, busy, onPark }: Pick<Props, "sign" | "gauges" | "busy" | "onPark">) {
  return (
    <section className="cs-sign" aria-label="The last parking sign">
      <div className="cs-photo">
        {sign ? (
          <img src={sign.url} alt={`The parking sign of block ${sign.block + 1}`} key={sign.url} />
        ) : (
          <p>The next sign is photographed when you pass it.</p>
        )}
        {sign && (
          <p className="cs-age">
            Block {sign.block + 1}
            {gauges.signAgoM !== undefined && gauges.block === sign.block && <span>{gauges.signAgoM} m ago</span>}
          </p>
        )}
      </div>
      <button type="button" className="cs-park" onClick={onPark} disabled={!sign || busy}>
        Park here?
      </button>
    </section>
  );
}

export function CenterScreen(props: Props) {
  const { style, busy, pedals, field, now, onSetClock, motorOn, onMotor, onSend } = props;
  const [text, setText] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    const said = text.trim();
    if (!said || busy) return;
    setText("");
    field.current?.blur(); // so that the up and down keys drive again
    onSend(said);
  }

  return (
    <section className="center-screen" style={style} aria-label="Center screen">
      <header className="cs-top">
        <Clock now={now} onSet={onSetClock} />
        <span className="cs-model">Spark</span>
        <button type="button" className="cs-speaker" aria-pressed={motorOn} aria-label="Motor sound" onClick={() => onMotor(!motorOn)}>
          <SpeakerIcon on={motorOn} />
        </button>
      </header>
      <div className="cs-main">
        <section className="cs-chat" aria-label={`Chat with ${ASSISTANT_NAME}`}>
          <Messages {...props} />
          <form className="cs-composer" onSubmit={submit}>
            <RunaAvatar />
            <input
              ref={field}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={`Talk to ${ASSISTANT_NAME}…`}
              aria-label="Message"
              autoComplete="off"
              enterKeyHint="send"
            />
            <button type="submit" aria-label="Send" disabled={busy || !text.trim()}>
              <SendIcon />
            </button>
          </form>
        </section>
        <SignCard {...props} />
        <PedalButtons pedals={pedals} />
      </div>
    </section>
  );
}
