import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { streamChat, streamToolResults } from "./chat";
import { fetchSourceUrl, openSource } from "./sources";
import { Chat } from "./components/Chat";
import { Header } from "./components/Header";
import { ConfigMissing, Loading, SignIn } from "./components/Notices";
import { UnderTheHood, readUnderTheHood, rememberUnderTheHood } from "./components/UnderTheHood";
import { readConfig, type AppConfig } from "./config";
import { runClientTool, type Theme } from "./tools";
import { chatReducer, initialChatState } from "./turns";
import type { Backend, ClientToolCall, SourceItem } from "./types";

// A client tool result can lead to another tool call, but not forever.
const MAX_TOOL_ROUNDS = 5;

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

export function Workspace({ config, auth, session }: { config: AppConfig; auth: Auth; session: AuthSession }) {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [backend, setBackend] = useState<Backend>("loop");
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [underTheHood, setUnderTheHood] = useState(readUnderTheHood);
  const [view, setView] = useState<"chat" | "timeline">("chat");
  const [sourceError, setSourceError] = useState<string>();
  const abort = useRef<AbortController | null>(null);
  const conversationId = useRef<string | undefined>(undefined);
  conversationId.current = state.conversationId;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => () => abort.current?.abort(), []);

  const applyTheme = useCallback((next: Theme) => {
    setTheme(next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* storage may be unavailable */
    }
  }, []);

  const send = useCallback(
    async (message: string) => {
      setBusy(true);
      const correlationId = crypto.randomUUID();
      dispatch({ type: "start", message, backend, correlationId });
      const ctrl = new AbortController();
      abort.current = ctrl;
      try {
        const token = await auth.getToken();
        let events = streamChat(
          {
            apiUrl: config.apiUrl,
            token,
            backend,
            conversationId: conversationId.current,
            message,
            correlationId,
          },
          ctrl.signal,
        );
        for (let round = 0; round <= MAX_TOOL_ROUNDS; round += 1) {
          let paused: { turnId: string; conversationId: string; calls: ClientToolCall[] } | undefined;
          for await (const event of events) {
            dispatch({ type: "event", event });
            if (event.type === "client_tool_calls" && event.data.turn_id && event.data.conversation_id) {
              paused = {
                turnId: event.data.turn_id,
                conversationId: event.data.conversation_id,
                calls: event.data.calls,
              };
            }
          }
          if (!paused) break;
          const results = paused.calls.map((call) => ({
            call_id: call.call_id,
            output: runClientTool(call, { setTheme: applyTheme }),
          }));
          events = streamToolResults(
            {
              apiUrl: config.apiUrl,
              token: await auth.getToken(),
              backend,
              conversationId: paused.conversationId,
              turnId: paused.turnId,
              correlationId,
              results,
            },
            ctrl.signal,
          );
        }
      } catch (e) {
        dispatch({
          type: "event",
          event: { type: "error", data: { code: "auth", message: e instanceof Error ? e.message : "Could not get a token." } },
        });
      } finally {
        setBusy(false);
      }
    },
    [applyTheme, auth, backend, config.apiUrl],
  );

  function openDocument(source: SourceItem) {
    if (!source.link) return;
    const link = source.link;
    setSourceError(undefined);
    openSource(async () => fetchSourceUrl(config.apiUrl, await auth.getToken(), link)).catch((e: unknown) =>
      setSourceError(e instanceof Error ? e.message : "The document could not be opened."),
    );
  }

  function toggleUnderTheHood() {
    setUnderTheHood(!underTheHood);
    rememberUnderTheHood(!underTheHood);
    setView("chat"); // on a small screen the panel has a tab of its own
  }

  function toggleTheme() {
    applyTheme(theme === "dark" ? "light" : "dark");
  }

  return (
    <div className="app" data-view={view} data-hood={underTheHood}>
      <Header
        session={session}
        onSignOut={() => void auth.signOut()}
        theme={theme}
        onToggleTheme={toggleTheme}
        underTheHood={underTheHood}
        onToggleUnderTheHood={toggleUnderTheHood}
        onNewChat={() => dispatch({ type: "reset" })}
        busy={busy}
      />
      {underTheHood && (
        <nav className="tabs" aria-label="View">
          <button className={view === "chat" ? "on" : ""} aria-pressed={view === "chat"} onClick={() => setView("chat")}>
            Chat
          </button>
          <button className={view === "timeline" ? "on" : ""} aria-pressed={view === "timeline"} onClick={() => setView("timeline")}>
            Timeline
          </button>
        </nav>
      )}
      <main className="panes">
        <Chat
          state={state}
          busy={busy}
          underTheHood={underTheHood}
          onSend={(t) => {
            setSourceError(undefined);
            void send(t);
          }}
          onOpenSource={openDocument}
          sourceError={sourceError}
        />
        {underTheHood && <UnderTheHood state={state} backend={backend} onBackend={setBackend} busy={busy} />}
      </main>
    </div>
  );
}
