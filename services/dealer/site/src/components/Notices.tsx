// The shape of clients/dashboard/src/components/Notices.tsx; keep the look of the two alike.
import type { Auth, AuthSession } from "../auth";

export function ConfigMissing({ missing }: { missing: string[] }) {
  return (
    <main className="gate">
      <div className="gate-card">
        <h1>The page could not start</h1>
        <p>It reads its settings from the dealer system. These are missing:</p>
        <ul className="missing">
          {missing.map((m) => (
            <li key={m}>
              <code>{m}</code>
            </li>
          ))}
        </ul>
        <button type="button" className="primary" onClick={() => location.reload()}>
          Try again
        </button>
      </div>
    </main>
  );
}

export function SignIn({ auth, error }: { auth: Auth; error?: string }) {
  return (
    <main className="gate">
      <div className="gate-card">
        <h1>Orders and inventory</h1>
        <p>See the orders that Runa places, deliver them, and keep the stock of each dealer.</p>
        {error && <p className="notice error">{error}</p>}
        <button type="button" className="primary" onClick={() => void auth.signIn()}>
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

/** The service answered 403 to a signed-in account. */
export function NoAccess({ session }: { session: AuthSession }) {
  return (
    <main className="gate">
      <div className="gate-card">
        <h1>This account has no access</h1>
        <p>{`You are signed in as ${session.username}. Ask the person who runs this deployment for the role Dealer.Manage, then sign in again.`}</p>
      </div>
    </main>
  );
}

/** What went wrong, over what the page still shows. */
export function Notices({ problem, notice, onClose }: { problem?: string; notice?: string; onClose: () => void }) {
  return (
    <div className="notices" role="alert">
      {problem && <p className="notice error">{`${problem} What you see may be old.`}</p>}
      {notice && notice !== problem && (
        <p className="notice error">
          <span>{notice}</span>
          <button type="button" className="ghost" aria-label="Close the notice" onClick={onClose}>
            Close
          </button>
        </p>
      )}
    </div>
  );
}
