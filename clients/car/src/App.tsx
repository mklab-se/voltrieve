// App and Authed are copied from clients/web; keep the two alike.
import { useEffect, useState } from "react";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { Chat } from "./components/Chat";
import { ConfigMissing, Loading, SignIn } from "./components/Notices";
import { readConfig, type AppConfig } from "./config";
import { carContext } from "./context";
import { fetchSourceUrl, openSource } from "./sources";
import CAR_TOOLS from "./tools.json";
import type { ClientToolCall, SourceItem } from "./types";
import { useChat } from "./useChat";

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

async function runTool(call: ClientToolCall) {
  return { output: `Error: the car cannot run ${call.name} yet.` };
}

const context = () => carContext({ distanceM: 0, speedKmh: 0 }, new Date());

export function Workspace({ config, auth, session }: { config: AppConfig; auth: Auth; session: AuthSession }) {
  const { state, busy, send, reset } = useChat({ auth, config, backend: "loop", tools: CAR_TOOLS, runTool, context });
  const [sourceError, setSourceError] = useState<string>();

  function openDocument(source: SourceItem) {
    if (!source.link) return;
    const link = source.link;
    setSourceError(undefined);
    openSource(async () => fetchSourceUrl(config.apiUrl, await auth.getToken(), link)).catch((e: unknown) =>
      setSourceError(e instanceof Error ? e.message : "The document could not be opened."),
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-name">Voltrieve</span>
          <span className="brand-sub">Spark</span>
        </div>
        <div className="actions">
          <button className="ghost" onClick={reset}>
            New chat
          </button>
          <button className="ghost" onClick={() => void auth.signOut()} title={`Signed in as ${session.username}`}>
            Sign out
          </button>
        </div>
      </header>
      <Chat
        state={state}
        busy={busy}
        onSend={(text) => {
          setSourceError(undefined);
          send(text);
        }}
        onOpenSource={openDocument}
        sourceError={sourceError}
      />
    </div>
  );
}
