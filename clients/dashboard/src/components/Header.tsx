import type { AuthSession } from "../auth";

interface Props {
  session: AuthSession;
  onSignOut: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}

export function Header({ session, onSignOut, theme, onToggleTheme }: Props) {
  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
          <path d="M18 3 7 18h7l-1 11 11-15h-7z" fill="currentColor" />
        </svg>
        <span className="brand-name">Voltrieve</span>
        <span className="brand-sub">Operations</span>
      </div>
      <div className="actions">
        <button className="ghost icon" onClick={onToggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Toggle theme">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button className="ghost who" onClick={onSignOut} title={`Signed in as ${session.username}. Click to sign out.`}>
          <span className="who-name">{session.name}</span>
          <span className="who-out">Sign out</span>
        </button>
      </div>
    </header>
  );
}
