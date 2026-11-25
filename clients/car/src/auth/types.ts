// Copied from clients/web; keep the two alike. Only the car has getTokenSilently and SignInNeeded.
export interface AuthSession {
  name: string;
  username: string;
}

/** The only surface the rest of the app sees. Everything MSAL-specific stays behind it. */
export interface Auth {
  readonly kind: "entra" | "fake";
  /** Completes any pending redirect and returns the signed-in account, if any. */
  init(): Promise<AuthSession | null>;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  /** A fresh access token for the Web API. */
  getToken(): Promise<string>;
  /** The same, and never leaves the page for it: a SignInNeeded instead. */
  getTokenSilently(): Promise<string>;
}

/** There is no token without the user. */
export class SignInNeeded extends Error {
  constructor() {
    super("Sign in again.");
  }
}
