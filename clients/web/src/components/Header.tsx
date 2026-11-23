import { useEffect, useRef, type FocusEvent, type KeyboardEvent } from "react";
import type { AuthSession } from "../auth";

interface AccountProps {
  session: AuthSession;
  onSignOut: () => void;
  underTheHood: boolean;
  onToggleUnderTheHood: () => void;
}

interface Props extends AccountProps {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onNewChat: () => void;
  busy: boolean;
}

export function Header({ theme, onToggleTheme, onNewChat, busy, ...account }: Props) {
  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
          <path d="M18 3 7 18h7l-1 11 11-15h-7z" fill="currentColor" />
        </svg>
        <span className="brand-name">My Voltrieve</span>
      </div>
      <div className="actions">
        <button className="ghost" onClick={onNewChat} disabled={busy}>
          New chat
        </button>
        <button className="ghost icon" onClick={onToggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <AccountMenu {...account} />
      </div>
    </header>
  );
}

function AccountMenu({ session, onSignOut, underTheHood, onToggleUnderTheHood }: AccountProps) {
  const menu = useRef<HTMLDetailsElement>(null);

  function close() {
    if (menu.current) menu.current.open = false;
  }

  useEffect(() => {
    const onPointer = (e: PointerEvent) => {
      if (!menu.current?.contains(e.target as Node)) close();
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, []);

  function onKey(e: KeyboardEvent) {
    if (e.key !== "Escape" || !menu.current?.open) return;
    close();
    menu.current.querySelector("summary")?.focus();
  }

  function onBlur(e: FocusEvent) {
    if (e.relatedTarget && !menu.current?.contains(e.relatedTarget)) close();
  }

  return (
    <details className="account" ref={menu} onKeyDown={onKey} onBlur={onBlur}>
      <summary className="ghost" aria-label={`Account of ${session.name}`}>
        <span className="initial" aria-hidden="true">
          {session.name.slice(0, 1).toUpperCase()}
        </span>
        <span className="account-name">{session.name}</span>
      </summary>
      <div className="account-menu">
        <p className="account-id">
          Signed in as <span>{session.username}</span>
        </p>
        <button type="button" className="switch" role="switch" aria-checked={underTheHood} onClick={onToggleUnderTheHood}>
          Under the hood
          <span className="switch-track" aria-hidden="true" />
        </button>
        <button type="button" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </details>
  );
}
