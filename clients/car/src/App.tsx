// App and Authed are copied from clients/web; keep the two alike.
import { useCallback, useEffect, useState } from "react";
import { uploadPhoto } from "./attachments";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { Chat } from "./components/Chat";
import { ConfigMissing, Loading, SignIn } from "./components/Notices";
import { readConfig, type AppConfig } from "./config";
import { carContext } from "./context";
import { Drive } from "./drive";
import { createHorn } from "./horn";
import { drawSign, layoutSign, signDef } from "./sim/signs";
import { signOf } from "./sim/street";
import { fetchSourceUrl, openSource } from "./sources";
import { CAR_TOOLS, runCarTool } from "./tools";
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

const PHOTO_PX = 1024;

/** The sign of a block, straight on. The street gets a camera that takes a real photo of it. */
async function photographSign(block: number): Promise<Blob> {
  const def = signDef(signOf(block));
  const { width, height } = layoutSign(def, 64);
  // As large as it can be: a small sign on a plain ground counts as blurred in the API.
  const sign = drawSign(def, Math.floor((64 * 0.9 * PHOTO_PX) / Math.max(width, height)));
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = PHOTO_PX;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#cfd6db";
  ctx.fillRect(0, 0, PHOTO_PX, PHOTO_PX);
  ctx.drawImage(sign, (PHOTO_PX - sign.width) / 2, (PHOTO_PX - sign.height) / 2);
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The photo could not be taken."))), "image/jpeg", 0.85),
  );
}

/** The car, and one line about what it does. */
function useDrive(config: AppConfig, auth: Auth): { drive: Drive; status: string } {
  const [drive] = useState(
    () =>
      new Drive(createHorn(), photographSign, async ({ block, blob }) =>
        uploadPhoto({ apiUrl: config.apiUrl, token: await auth.getToken(), blob, label: `Parking sign in block ${block}` }),
      ),
  );
  const [status, setStatus] = useState("");
  useEffect(() => {
    let frame = 0;
    let before = performance.now();
    const onFrame = (now: number) => {
      drive.tick((now - before) / 1000);
      before = now;
      setStatus(`${Math.round(drive.speedKmh())} km/h, ${drive.car.mode}, wipers ${drive.wipers}`);
      frame = requestAnimationFrame(onFrame);
    };
    frame = requestAnimationFrame(onFrame);
    return () => cancelAnimationFrame(frame);
  }, [drive]);
  return { drive, status };
}

export function Workspace({ config, auth, session }: { config: AppConfig; auth: Auth; session: AuthSession }) {
  const { drive, status } = useDrive(config, auth);
  const runTool = useCallback((call: ClientToolCall) => runCarTool(call, drive), [drive]);
  const context = useCallback(() => carContext(drive.car, new Date()), [drive]);
  const renew = useCallback((ids: string[]) => drive.renewPhotos(ids), [drive]);
  const chat = useChat({ auth, config, backend: "loop", tools: CAR_TOOLS, runTool, context, renew });
  const { state, busy, send, reset } = chat;
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
        <span className="muted">{status}</span>
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
