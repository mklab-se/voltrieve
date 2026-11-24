import type { KeyboardEvent } from "react";
import type { AuthSession } from "../auth";
import { nextTab, TABS, type Tab } from "../insights";

interface Props {
  session: AuthSession;
  onSignOut: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  /** The chosen tab. No tabs are shown without it, as before access is allowed. */
  tab?: Tab;
  onTab?: (tab: Tab) => void;
}

export function Header({ session, onSignOut, theme, onToggleTheme, tab, onTab }: Props) {
  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
          <path d="M18 3 7 18h7l-1 11 11-15h-7z" fill="currentColor" />
        </svg>
        <span className="brand-name">Voltrieve</span>
        <span className="brand-sub">Operations</span>
      </div>
      {tab && <Tabs tab={tab} onTab={(next) => onTab?.(next)} />}
      <div className="actions">
        <button className="ghost icon" onClick={onToggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            {theme === "dark" ? (
              <path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
            ) : (
              <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z" />
            )}
          </svg>
        </button>
        <button className="ghost who" onClick={onSignOut} title={`Signed in as ${session.username}. Click to sign out.`}>
          <span className="who-name">{session.name}</span>
          <span className="who-out">Sign out</span>
        </button>
      </div>
    </header>
  );
}

function Tabs({ tab, onTab }: { tab: Tab; onTab: (tab: Tab) => void }) {
  function onKeyDown(e: KeyboardEvent) {
    const next = nextTab(tab, e.key);
    if (!next) return;
    e.preventDefault();
    onTab(next);
    document.getElementById(`tab-${next}`)?.focus();
  }
  return (
    <div className="tabs" role="tablist" aria-label="Pages">
      {TABS.map(([id, name]) => (
        <button
          key={id}
          id={`tab-${id}`}
          role="tab"
          aria-selected={tab === id}
          aria-controls="panel"
          tabIndex={tab === id ? 0 : -1}
          onClick={() => onTab(id)}
          onKeyDown={onKeyDown}
        >
          {name}
        </button>
      ))}
    </div>
  );
}
