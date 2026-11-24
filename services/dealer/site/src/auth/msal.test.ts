import { beforeEach, describe, expect, it, vi } from "vitest";

const silent = vi.fn();
const redirect = vi.fn();
vi.mock("@azure/msal-browser", () => {
  class InteractionRequiredAuthError extends Error {}
  class PublicClientApplication {
    initialize = async () => {};
    handleRedirectPromise = async () => ({ account: { name: "Anna Rep", username: "anna@voltrieve.example" } });
    getActiveAccount = () => null;
    getAllAccounts = () => [];
    setActiveAccount = () => {};
    acquireTokenSilent = silent;
    acquireTokenRedirect = redirect;
  }
  return { InteractionRequiredAuthError, PublicClientApplication };
});
const { InteractionRequiredAuthError } = await import("@azure/msal-browser");
const { createMsalAuth } = await import("./msal");

async function signedIn() {
  vi.stubGlobal("window", { location: { origin: "https://dealer.example" } });
  const auth = createMsalAuth({ tenantId: "t", clientId: "c", scope: "s", fakeAuth: false });
  await auth.init();
  return auth;
}
/** Where a promise is after everything that can go on has gone on. */
async function state(p: Promise<string>): Promise<string> {
  let now = "waiting";
  p.then((v) => (now = `got ${v}`), (e: Error) => (now = `failed ${e.message}`));
  await new Promise((resolve) => setTimeout(resolve));
  return now;
}

beforeEach(() => {
  silent.mockReset();
  redirect.mockReset();
});

describe("a token", () => {
  it("comes without a redirect when it can", async () => {
    silent.mockResolvedValue({ accessToken: "a" });
    expect(await (await signedIn()).getToken()).toBe("a");
    expect(redirect).not.toHaveBeenCalled();
  });

  it("that needs the user makes one redirect, also for several callers and later ones", async () => {
    silent.mockRejectedValue(new (InteractionRequiredAuthError as unknown as new () => Error)());
    redirect.mockResolvedValueOnce(undefined).mockRejectedValue(new Error("interaction_in_progress"));
    const auth = await signedIn();
    const waiting = [auth.getToken(), auth.getToken()];
    expect(await Promise.all(waiting.map(state))).toEqual(["waiting", "waiting"]);
    expect(await state(auth.getToken())).toBe("waiting");
    expect(redirect).toHaveBeenCalledTimes(1);
  });

  it("can be asked for again when the redirect did not start", async () => {
    silent.mockRejectedValue(new (InteractionRequiredAuthError as unknown as new () => Error)());
    redirect.mockRejectedValueOnce(new Error("no network")).mockResolvedValueOnce(undefined);
    const auth = await signedIn();
    expect(await state(auth.getToken())).toBe("failed no network");
    expect(await state(auth.getToken())).toBe("waiting");
    expect(redirect).toHaveBeenCalledTimes(2);
  });

  it("that fails in another way makes no redirect", async () => {
    silent.mockRejectedValue(new Error("timed out"));
    expect(await state((await signedIn()).getToken())).toBe("failed timed out");
    expect(redirect).not.toHaveBeenCalled();
  });
});
