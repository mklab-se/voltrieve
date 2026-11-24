import { useEffect, useMemo, useState } from "react";
import { createApi, type Api } from "./api";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { Confirm } from "./components/Confirm";
import { Header } from "./components/Header";
import { Inventory } from "./components/Inventory";
import { Log } from "./components/Log";
import { ConfigMissing, Loading, NoAccess, Notices, SignIn } from "./components/Notices";
import { Orders } from "./components/Orders";
import { loadConfig, type ConfigResult } from "./config";
import { RESET, useDealer } from "./state";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

type Boot = { status: "starting" } | { status: "missing"; missing: string[] } | { status: "ready"; auth: Auth; session: AuthSession | null };

async function start(): Promise<Boot> {
  const fakeAuth = import.meta.env.DEV && import.meta.env.VITE_DEV_FAKE_AUTH === "true";
  const result: ConfigResult = await loadConfig(fakeAuth);
  if (!result.ok) return { status: "missing", missing: result.missing };
  try {
    return { status: "ready", ...(await bootAuth(result.config)) };
  } catch (e) {
    return { status: "missing", missing: [e instanceof Error ? e.message : "Sign-in could not start."] };
  }
}

export function App() {
  const [boot, setBoot] = useState<Boot>({ status: "starting" });
  useEffect(() => {
    let cancelled = false;
    void start().then((b) => !cancelled && setBoot(b));
    return () => {
      cancelled = true;
    };
  }, []);

  if (boot.status === "starting") return <Loading />;
  if (boot.status === "missing") return <ConfigMissing missing={boot.missing} />;
  if (!boot.session) return <SignIn auth={boot.auth} />;
  return <Workspace auth={boot.auth} session={boot.session} />;
}

function Workspace({ auth, session }: { auth: Auth; session: AuthSession }) {
  const api = useMemo(() => createApi(auth), [auth]);
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* storage may be unavailable */
    }
  }

  return <Desk api={api} session={session} theme={theme} onToggleTheme={toggleTheme} onSignOut={() => void auth.signOut()} />;
}

interface DeskProps {
  api: Api;
  session: AuthSession;
  theme: Theme;
  onToggleTheme: () => void;
  onSignOut: () => void;
}

/** The page itself, for a signed-in account. */
export function Desk({ api, session, theme, onToggleTheme, onSignOut }: DeskProps) {
  const dealer = useDealer(api);
  const { state, denied, live, lost } = dealer;
  const [asking, setAsking] = useState(false);

  return (
    <div className="app">
      <Header
        session={session}
        onSignOut={onSignOut}
        theme={theme}
        onToggleTheme={onToggleTheme}
        connection={denied ? undefined : live ? "live" : lost ? "lost" : "connecting"}
      />
      {denied ? (
        <NoAccess session={session} />
      ) : (
        <main className="desk">
          <Notices problem={dealer.problem} notice={dealer.notice} onClose={dealer.dismissNotice} />
          {state ? (
            <>
              <Orders state={state} busy={dealer.busy} onDeliver={dealer.deliver} onDelete={dealer.remove} />
              <Inventory state={state} flashes={dealer.flashes} onChange={(model, city, change) => void dealer.changeStock(model, city, change)} />
              <Log state={state} />
              <footer className="desk-foot">
                <button type="button" className="ghost" disabled={dealer.busy.has(RESET)} onClick={() => setAsking(true)}>
                  Reset the demo
                </button>
              </footer>
            </>
          ) : (
            !dealer.problem && <p className="empty">Reading orders and stock.</p>
          )}
        </main>
      )}
      {asking && !denied && (
        <Confirm
          title="Reset the demo?"
          text="All orders are deleted, the stock goes back to where it started, and the log is emptied."
          keep="Keep everything"
          go="Reset it"
          onKeep={() => setAsking(false)}
          onGo={() => {
            setAsking(false);
            void dealer.reset();
          }}
        />
      )}
    </div>
  );
}
