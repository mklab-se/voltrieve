// Copied from clients/dashboard/src/auth/msal.ts; keep the two alike, except that here several callers
// can ask for a token at once, and they share one redirect.
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from "@azure/msal-browser";
import type { AppConfig } from "../config";
import type { Auth, AuthSession } from "./types";

function toSession(a: AccountInfo): AuthSession {
  return { name: a.name ?? a.username, username: a.username };
}

/** Authorization code flow with PKCE (the msal-browser default), via redirect. */
export function createMsalAuth(config: AppConfig): Auth {
  const app = new PublicClientApplication({
    auth: {
      clientId: config.clientId,
      authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  const request = { scopes: [config.scope] };
  let account: AccountInfo | null = null;
  let redirecting: Promise<string> | undefined;

  return {
    kind: "entra",
    async init() {
      await app.initialize();
      const result = await app.handleRedirectPromise();
      account = result?.account ?? app.getActiveAccount() ?? app.getAllAccounts()[0] ?? null;
      if (account) app.setActiveAccount(account);
      return account ? toSession(account) : null;
    },
    async signIn() {
      await app.loginRedirect(request);
    },
    async signOut() {
      await app.logoutRedirect({ account: account ?? undefined });
    },
    async getToken() {
      if (!account) throw new Error("Not signed in.");
      if (redirecting) return redirecting;
      try {
        return (await app.acquireTokenSilent({ ...request, account })).accessToken;
      } catch (e) {
        if (e instanceof InteractionRequiredAuthError) {
          redirecting ??= app.acquireTokenRedirect({ ...request, account }).then(
            // The page is navigating away; never settle so no error flashes.
            () => new Promise<string>(() => {}),
            (failed) => {
              redirecting = undefined;
              throw failed;
            },
          );
          return redirecting;
        }
        throw e;
      }
    },
  };
}
