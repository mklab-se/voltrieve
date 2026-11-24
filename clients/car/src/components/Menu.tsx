import { useEffect, useRef, useState, type ReactNode } from "react";
import { MenuIcon } from "./icons";
import { ASSISTANT_NAME } from "./Runa";

interface Props {
  onNewChat: () => void;
  onSignOut: () => void;
  username: string;
  children?: ReactNode;
}

/** The menu in the corner. */
export function Menu({ onNewChat, onSignOut, username, children }: Props) {
  const [open, setOpen] = useState(false);
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
          {children}
          <button type="button" onClick={choose(onSignOut)} title={`Signed in as ${username}`}>
            Sign out
          </button>
          <p>
            You are talking to {ASSISTANT_NAME}, an AI assistant. During demos, questions may be shown on a live screen, with
            contact details masked. They are not stored.
          </p>
        </div>
      )}
    </div>
  );
}
