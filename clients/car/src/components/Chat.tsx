// Copied from clients/web; keep the two alike.
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { ChatState } from "../turns";
import type { SourceItem } from "../types";
import { Answer } from "./Answer";
import { ASSISTANT_NAME, RunaAvatar } from "./Runa";
import { SourceList } from "./SourceList";

const STARTERS = [
  "Can I park here?",
  "Turn on the wipers.",
  "How far does the Spark go on a full battery?",
];

interface Props {
  state: ChatState;
  busy: boolean;
  onSend: (text: string) => void;
  onOpenSource: (source: SourceItem) => void;
  /** Why the last source could not be opened. */
  sourceError?: string;
}

export function Chat({ state, busy, onSend, onOpenSource, sourceError }: Props) {
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
    <section className="chat" aria-label="Chat">
      <div className="messages" aria-live="polite">
        {state.turns.length === 0 && (
          <div className="empty">
            <RunaAvatar size="large" />
            <h2>Ask {ASSISTANT_NAME} something</h2>
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
          placeholder={`Ask ${ASSISTANT_NAME}…`}
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
