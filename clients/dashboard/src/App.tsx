import { useEffect, useState } from "react";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { Dashboard } from "./components/Dashboard";
import { Header } from "./components/Header";
import { ConfigMissing, Loading, NoRole, SignIn } from "./components/Notices";
import { readConfig, type AppConfig } from "./config";
import { checkAccess } from "./dashboard";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function App() {
  const result = readConfig(import.meta.env as Record<string, string | undefined>, import.meta.env.DEV);
  if (!result.ok) return <ConfigMissing missing={result.missing} />;
  return <Authed config={result.config} />;
}

function Authed({ config }: { config: AppConfig }) {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [error, setError] = useState<string>();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { auth: a, session: s } = await bootAuth(config);
        if (cancelled) return;
        setAuth(a);
        setSession(s);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Sign-in could not start.");
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [config]);

  if (!ready) return <Loading />;
  if (!auth) return <ConfigMissing missing={[error ?? "authentication failed to initialise"]} />;
  if (!session) return <SignIn auth={auth} error={error} />;
  return <Workspace config={config} auth={auth} session={session} />;
}

function Workspace({ config, auth, session }: { config: AppConfig; auth: Auth; session: AuthSession }) {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  // null while the token is checked. The feed is not asked for until the role is there.
  const [allowed, setAllowed] = useState<boolean | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    void checkAccess(auth).then((ok) => !cancelled && setAllowed(ok));
    return () => {
      cancelled = true;
    };
  }, [auth]);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* storage may be unavailable */
    }
  }

  return (
    <div className="app">
      <Header session={session} onSignOut={() => void auth.signOut()} theme={theme} onToggleTheme={toggleTheme} />
      {allowed === null && <Loading />}
      {allowed === false && <NoRole />}
      {allowed === true && (
        <Dashboard apiUrl={config.apiUrl} auth={auth} liveMetricsUrl={config.liveMetricsUrl} workbookUrl={config.workbookUrl} />
      )}
    </div>
  );
}
