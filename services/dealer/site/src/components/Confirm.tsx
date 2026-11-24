import { useId, useLayoutEffect, useRef } from "react";

interface Props {
  title: string;
  text: string;
  keep: string;
  go: string;
  onKeep: () => void;
  onGo: () => void;
}

/** Asks before something that cannot be undone. The browser keeps the focus inside, starts it on the
 *  safe button and gives it back on close. */
export function Confirm({ title, text, keep, go, onKeep, onGo }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  useLayoutEffect(() => {
    const d = dialog.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog
      className="confirm"
      ref={dialog}
      aria-labelledby={`${id}t`}
      aria-describedby={`${id}d`}
      onCancel={(e) => {
        e.preventDefault();
        onKeep();
      }}
      // A browser may close it without asking. The close above sends this too, late, when it is open again.
      onClose={() => !dialog.current?.open && onKeep()}
    >
      <h2 id={`${id}t`}>{title}</h2>
      <p id={`${id}d`}>{text}</p>
      <div className="confirm-buttons">
        <button type="button" className="ghost" onClick={onKeep}>
          {keep}
        </button>
        <button type="button" className="danger" onClick={onGo}>
          {go}
        </button>
      </div>
    </dialog>
  );
}
