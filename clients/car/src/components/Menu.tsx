import { useEffect, useRef, useState } from "react";
import { MenuIcon } from "./icons";
import { ASSISTANT_NAME } from "./Runa";

interface Props {
  onNewChat: () => void;
  onSignOut: () => void;
  username: string;
  underTheHood: boolean;
  onUnderTheHood: (on: boolean) => void;
  remote: boolean;
  onRemote: (on: boolean) => void;
  /** For the tests, which cannot click. */
  openAtFirst?: boolean;
}

/** The menu in the corner. */
export function Menu(props: Props) {
  const { onNewChat, onSignOut, username, underTheHood, onUnderTheHood, remote, onRemote, openAtFirst = false } = props;
  const [open, setOpen] = useState(openAtFirst);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);
  const choose = (what: () => void) => () => {
    setOpen(false);
    what();
  };
  return (
    <div
      className="menu"
      ref={box}
      onKeyDown={(e) => {
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        setOpen(false);
      }}
    >
      <button type="button" className="menu-button" aria-label="Menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <MenuIcon />
      </button>
      {open && (
        <div className="menu-sheet">
          <button type="button" onClick={choose(onNewChat)}>
            New chat
          </button>
          <button type="button" className="switch" role="switch" aria-checked={underTheHood} onClick={choose(() => onUnderTheHood(!underTheHood))}>
            Under the hood
            <span className="switch-track" aria-hidden="true" />
          </button>
          <button type="button" className="switch" role="switch" aria-checked={remote} onClick={choose(() => onRemote(!remote))}>
            Remote control
            <span className="switch-track" aria-hidden="true" />
          </button>
          <button type="button" onClick={choose(onSignOut)} title={`Signed in as ${username}`}>
            Sign out
          </button>
          <p>
            You are talking to {ASSISTANT_NAME}, an AI assistant. During demos, questions may be shown on a live screen, with
            contact details masked. The live screen keeps nothing, but your chat is kept by the service. A nightly
            job counts what people ask about. It saves only topics shared by several people, never your chat.
          </p>
        </div>
      )}
    </div>
  );
}
