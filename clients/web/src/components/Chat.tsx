import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatState } from "../turns";
import type { SourceItem } from "../types";
import { Answer } from "./Answer";
import { ASSISTANT_NAME, RunaAvatar } from "./Runa";
import { SourceList } from "./SourceList";

const TITLE = `Ask ${ASSISTANT_NAME} about your car`;

const STARTERS = [
  "How fast can I charge at home?",
  "When is my next service and what is done?",
  "Which winter wheels fit my car?",
  "What does my warranty cover?",
];

interface Props {
  state: ChatState;
  busy: boolean;
  /** Whether an answer names the backend that made it. */
  underTheHood: boolean;
  onSend: (text: string) => void;
  onOpenSource: (source: SourceItem) => void;
  /** Why the last source could not be opened. */
  sourceError?: string;
}

export function Chat({ state, busy, underTheHood, onSend, onOpenSource, sourceError }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const last = state.turns[state.turns.length - 1];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [state.turns.length, last?.answer, last?.status]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText("");
    onSend(t);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
  }

  return (
    <section className="chat" aria-label={TITLE}>
      {state.turns.length > 0 && (
        <div className="chat-head">
          <RunaAvatar />
          <h2>{TITLE}</h2>
        </div>
      )}
      <div className="messages" aria-live="polite">
        {state.turns.length === 0 && (
          <div className="empty">
            <RunaAvatar size="large" />
            <h2>{TITLE}</h2>
            <p className="muted">Charging, service, tyres or warranty: she looks it up in the Voltrieve documents.</p>
            <div className="starters">
              {STARTERS.map((s) => (
                <button key={s} className="chip" onClick={() => onSend(s)} disabled={busy}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {state.turns.map((t) => (
          <div className="exchange" key={t.id}>
            <div className="bubble user">{t.userText}</div>
            <div className={`bubble agent ${t.status}`}>
              <p className="who">
                <RunaAvatar />
                {ASSISTANT_NAME}
              </p>
              {t.answer ? (
                <Answer text={t.answer} sources={t.sources} onOpenSource={onOpenSource} />
              ) : t.status === "streaming" ? (
                <p className="thinking" role="status" aria-label={t.activity ?? "Agent is working"}>
                  <i /><i /><i />
                  {t.activity && <span>{t.activity}</span>}
                </p>
              ) : null}
              {t.error && (
                <p className="notice error" role="alert">
                  {t.error}
                </p>
              )}
              {t.sources && t.sources.length > 0 && <SourceList sources={t.sources} onOpen={onOpenSource} />}
              {underTheHood && t.backend && <p className="meta">via {t.backend} · turn {t.id}</p>}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      {sourceError && (
        <p className="notice error source-error" role="alert">
          {sourceError}
        </p>
      )}
      <form className="composer" onSubmit={submit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={`${TITLE}…`}
          rows={1}
          aria-label="Message"
        />
        <button className="primary" type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
      <p className="privacy-note">
        You are talking to {ASSISTANT_NAME}, an AI assistant. During demos, questions may be shown on a live screen, with contact
        details masked. They are not stored.
      </p>
    </section>
  );
}
