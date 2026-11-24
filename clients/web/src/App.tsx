import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { PhotoNotSent, withPhoto } from "./attachments";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { streamChat, streamToolResults } from "./chat";
import { errorOf, withSignIn } from "./errors";
import { fetchSourceUrl, openSource } from "./sources";
import { Chat } from "./components/Chat";
import { Header } from "./components/Header";
import { MyCar } from "./components/MyCar";
import { ConfigMissing, Loading, SignIn } from "./components/Notices";
import { UnderTheHood, readUnderTheHood, rememberUnderTheHood } from "./components/UnderTheHood";
import { readConfig, type AppConfig } from "./config";
import { webContext } from "./garage";
import { runClientTool, type Theme } from "./tools";
import { runTurn } from "./turn";
import { chatReducer, initialChatState } from "./turns";
import type { Backend, SourceItem } from "./types";
import { usePhoto } from "./usePhoto";

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
  const photo = usePhoto(config.apiUrl, auth);
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

  const { upload, lost, sent, over } = photo;
  const attached = photo.attached.photo;
  const send = useCallback(
    async (message: string) => {
      setBusy(true);
      const withKey = attached?.id ? attached.key : undefined; // the photo this message names
      let failed = false;
      let withdrawn = false;
      const correlationId = crypto.randomUUID();
      dispatch({ type: "start", message, backend, correlationId, photo: attached?.blob });
      if (withKey !== undefined) sent(withKey);
      const ctrl = new AbortController();
      abort.current = ctrl;
      const getToken = withSignIn(() => auth.getToken());
      try {
        const token = await getToken();
        const request = {
          apiUrl: config.apiUrl,
          token,
          backend,
          conversationId: conversationId.current,
          message,
          correlationId,
          context: webContext(new Date()),
        };
        const ask = (attachments: string[]) => streamChat({ ...request, attachments }, ctrl.signal);
        await runTurn(withPhoto(attached, ask, upload, lost), {
          emit: (event) => {
            failed ||= event.type === "error";
            dispatch({ type: "event", event });
          },
          runTool: (call) => runClientTool(call, { setTheme: applyTheme }),
          resume: async ({ conversationId, turnId }, results) =>
            streamToolResults(
              { apiUrl: config.apiUrl, token: await getToken(), backend, conversationId, turnId, correlationId, results },
              ctrl.signal,
            ),
        });
      } catch (e) {
        failed = true;
        withdrawn = e instanceof PhotoNotSent;
        if (withdrawn) dispatch({ type: "withdraw" }); // the notice beside the field says why
        else dispatch({ type: "event", event: { type: "error", data: errorOf(e) } });
      } finally {
        if (withKey !== undefined) {
          over(withKey, !failed);
          if (failed && !withdrawn) dispatch({ type: "again", text: message });
        }
        setBusy(false);
      }
    },
    [applyTheme, attached, auth, backend, config.apiUrl, lost, over, sent, upload],
  );

  function openDocument(source: SourceItem) {
    if (!source.link) return;
    const link = source.link;
    setSourceError(undefined);
    const getToken = () => auth.getToken().catch(() => Promise.reject(new Error("You could not be signed in. Try again.")));
    openSource(async () => fetchSourceUrl(config.apiUrl, await getToken(), link)).catch((e: unknown) =>
      setSourceError(e instanceof Error ? e.message : "The document could not be opened. Try again."),
    );
  }

  function toggleUnderTheHood() {
    setUnderTheHood(!underTheHood);
    rememberUnderTheHood(!underTheHood);
    setView("chat"); // on a small screen the panel has a tab of its own
  }

  function newChat() {
    dispatch({ type: "reset" });
    photo.reset();
    setSourceError(undefined);
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
        onNewChat={newChat}
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
        <div className="portal">
          <div className="portal-body">
            <MyCar owner={session.name} />
            <Chat
              state={state}
              busy={busy}
              underTheHood={underTheHood}
              photo={photo}
              onDraft={(text) => dispatch({ type: "draft", text })}
              onSend={(t) => {
                setSourceError(undefined);
                if (!attached) photo.remove(); // why the last photo was refused is old news
                void send(t);
              }}
              onOpenSource={openDocument}
              sourceError={sourceError}
            />
          </div>
        </div>
        {underTheHood && <UnderTheHood state={state} backend={backend} onBackend={setBackend} busy={busy} />}
      </main>
    </div>
  );
}
