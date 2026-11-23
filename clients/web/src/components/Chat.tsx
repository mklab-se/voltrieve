// Copied to clients/car, without photos; keep the rest alike.
import { useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import { dropped } from "../attachments";
import type { ChatState } from "../turns";
import type { SourceItem } from "../types";
import type { PhotoControls } from "../usePhoto";
import { Answer } from "./Answer";
import { NOT_STORED, PhotoButton, PhotoPreview, PhotoThumb } from "./PhotoButton";
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
  photo: PhotoControls;
  onDraft: (text: string) => void;
  onSend: (text: string) => void;
  onOpenSource: (source: SourceItem) => void;
  /** Why the last source could not be opened. */
  sourceError?: string;
}

export function Chat({ state, busy, underTheHood, photo, onDraft, onSend, onOpenSource, sourceError }: Props) {
  const [dropping, setDropping] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const photoButton = useRef<HTMLButtonElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const last = state.turns[state.turns.length - 1];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [state.turns.length, last?.answer, last?.status]);

  // A photo dropped beside the composer would replace the page.
  useEffect(() => {
    const keepPage = (e: globalThis.DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    window.addEventListener("dragover", keepPage);
    window.addEventListener("drop", keepPage);
    return () => {
      window.removeEventListener("dragover", keepPage);
      window.removeEventListener("drop", keepPage);
    };
  }, []);

  const attached = photo.attached.photo;
  const blocked = busy || (attached !== undefined && attached.status !== "ready");

  function ask(question: string) {
    const t = question.trim();
    if (!t || blocked) return;
    onSend(t);
    // The button that was pressed is disabled or gone now. On a phone this would open the keyboard.
    if (!matchMedia("(pointer: coarse)").matches) field.current?.focus();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    ask(state.draft);
  }

  function onDragOver(e: DragEvent) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = busy ? "none" : "copy";
    setDropping(!busy);
  }

  function onDragLeave(e: DragEvent) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDropping(false);
  }

  function onDrop(e: DragEvent) {
    const chosen = dropped([...e.dataTransfer.files]);
    if (!chosen) return;
    e.preventDefault();
    setDropping(false);
    if (!busy) photo.choose(chosen); // the photo of a question stays while it is answered
  }

  /** The preview's buttons go away when they are pressed, so the focus moves on. */
  function thenFocus(action: () => void) {
    photoButton.current?.focus();
    action();
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
            <p className="muted">Charging, service, tyres or warranty: Runa looks it up in the Voltrieve documents.</p>
            <div className="starters">
              {STARTERS.map((s) => (
                <button key={s} className="chip" onClick={() => ask(s)} disabled={blocked}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {state.turns.map((t) => (
          <div className="exchange" key={t.id}>
            <div className="bubble user">
              {t.photo && <PhotoThumb blob={t.photo} alt="Your photo" />}
              {t.userText}
            </div>
            <div className={`bubble agent ${t.status}`}>
              <p className="who">
                <RunaAvatar />
                {ASSISTANT_NAME}
              </p>
              {t.answer ? (
                <Answer text={t.answer} sources={t.sources} onOpenSource={onOpenSource} />
              ) : t.status === "streaming" ? (
                <p className="thinking" role="status" aria-label={t.activity ?? `${ASSISTANT_NAME} is working`}>
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
      <form
        className="composer"
        data-dropping={dropping}
        onSubmit={submit}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {photo.attached.notice && (
          <p className="notice error composer-notice" role="alert">
            {photo.attached.notice}
          </p>
        )}
        {attached && (
          <PhotoPreview photo={attached} disabled={busy} onRetry={() => thenFocus(photo.retry)} onRemove={() => thenFocus(photo.remove)} />
        )}
        <PhotoButton ref={photoButton} disabled={busy} onFile={(file) => photo.choose({ file })} />
        <textarea
          ref={field}
          value={state.draft}
          onChange={(e) => onDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder={`${TITLE}…`}
          rows={1}
          aria-label="Message"
        />
        <button className="primary" type="submit" disabled={blocked || !state.draft.trim()}>
          Send
        </button>
      </form>
      <p className="privacy-note">
        You are talking to {ASSISTANT_NAME}, an AI assistant. During demos, questions may be shown on a live screen, with contact
        details masked. They are not stored.{attached ? "" : ` ${NOT_STORED}`}
      </p>
    </section>
  );
}
