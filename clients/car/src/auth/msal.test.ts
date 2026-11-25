import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { beforeEach, describe, expect, it, vi } from "vitest";

const app = {
  initialize: vi.fn(async () => {}),
  handleRedirectPromise: vi.fn(async () => ({ account: { username: "dev@x" } })),
  setActiveAccount: vi.fn(),
  acquireTokenSilent: vi.fn(async (): Promise<{ accessToken: string }> => ({ accessToken: "token" })),
  acquireTokenRedirect: vi.fn(async () => {}),
};
vi.mock("@azure/msal-browser", async (original) => ({
  ...(await original<typeof import("@azure/msal-browser")>()),
  PublicClientApplication: function () {
    return app;
  },
}));
vi.stubGlobal("window", { location: { origin: "http://car" } });

const { createMsalAuth } = await import("./msal");
const { SignInNeeded } = await import("./types");
const config = { apiUrl: "http://api", tenantId: "t", clientId: "c", scope: "s", fakeAuth: false, remoteControl: true };

beforeEach(() => void vi.clearAllMocks());

describe("a token that comes silently", () => {
  it("comes like any other", async () => {
    const auth = createMsalAuth(config);
    await auth.init();
    expect(await auth.getTokenSilently()).toBe("token");
  });

  it("never leaves the page: it says that the driver has to sign in", async () => {
    const auth = createMsalAuth(config);
    await expect(auth.getTokenSilently()).rejects.toThrow(SignInNeeded);
    await auth.init();
    app.acquireTokenSilent.mockRejectedValueOnce(new InteractionRequiredAuthError("interaction_required", "Sign in."));
    await expect(auth.getTokenSilently()).rejects.toThrow(SignInNeeded);
    expect(app.acquireTokenRedirect).not.toHaveBeenCalled();
  });

  it("passes on what may work the next time", async () => {
    const auth = createMsalAuth(config);
    await auth.init();
    app.acquireTokenSilent.mockRejectedValueOnce(new TypeError("no network"));
    await expect(auth.getTokenSilently()).rejects.toThrow("no network");
  });
});
