// App and Authed are copied from clients/web; keep the two alike.
import { lazy, Suspense, useEffect, useState } from "react";
import { bootAuth, type Auth, type AuthSession } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ConfigMissing, Loading, SignIn } from "./components/Notices";
import { readConfig, type AppConfig } from "./config";

// The car comes after the sign-in, so that the sign-in page is light.
const Workspace = lazy(async () => ({ default: (await import("./Workspace")).Workspace }));

export function App() {
  const result = readConfig(import.meta.env as Record<string, string | undefined>, import.meta.env.DEV);
  if (!result.ok) return <ConfigMissing missing={result.missing} />;
  return (
    <ErrorBoundary>
      <Authed config={result.config} />
    </ErrorBoundary>
  );
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
  return (
    <Suspense fallback={<Loading />}>
      <Workspace config={config} auth={auth} session={session} />
    </Suspense>
  );
}
