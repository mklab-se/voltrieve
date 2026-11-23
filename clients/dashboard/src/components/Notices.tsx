// ConfigMissing and Loading are copied to clients/web/src/components/Notices.tsx; keep the two alike.
import type { Auth } from "../auth";
import { DASHBOARD_ROLE } from "../dashboard";

export function ConfigMissing({ missing }: { missing: string[] }) {
  return (
    <main className="gate">
      <div className="gate-card">
        <p className="eyebrow">Configuration</p>
        <h1>This build is not configured</h1>
        <p>
          The app reads its settings from build-time <code>VITE_*</code> variables. These are missing:
        </p>
        <ul className="missing">
          {missing.map((m) => (
            <li key={m}>
              <code>{m}</code>
            </li>
          ))}
        </ul>
        <p className="muted">
          Copy <code>.env.example</code> to <code>.env.local</code>, fill it in and restart. None of the values are secrets.
        </p>
      </div>
    </main>
  );
}

export function SignIn({ auth, error }: { auth: Auth; error?: string }) {
  return (
    <main className="gate">
      <div className="gate-card">
        <p className="eyebrow">Voltrieve · Operations</p>
        <h1>See what the agent is doing now</h1>
        <p>A live view of the questions people ask, how fast they are answered and which tools and knowledge bases are used.</p>
        {error && <p className="notice error">{error}</p>}
        <button className="primary" onClick={() => void auth.signIn()}>
          {auth.kind === "fake" ? "Continue as dev tester" : "Sign in with Microsoft"}
        </button>
        {auth.kind === "fake" && <p className="muted">Dev-only fake sign-in against the mock server.</p>}
      </div>
    </main>
  );
}

export function Loading() {
  return (
    <main className="gate">
      <p className="muted">Starting…</p>
    </main>
  );
}

/** Shown after sign-in to a user whose token lacks the role. The API checks the role too. */
export function NoRole() {
  return (
    <main className="gate">
      <div className="gate-card">
        <p className="eyebrow">Access</p>
        <h1>You need the {DASHBOARD_ROLE} role</h1>
        <p className="muted">Ask the person who runs this deployment to give you the role, then sign in again.</p>
      </div>
    </main>
  );
}
