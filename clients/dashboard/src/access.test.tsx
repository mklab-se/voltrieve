import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Auth } from "./auth";
import { checkAccess } from "./dashboard";
import { NoRole } from "./components/Notices";

function token(payload: object): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function authWith(accessToken: string): Auth {
  return { kind: "fake", init: async () => null, signIn: async () => {}, signOut: async () => {}, getToken: async () => accessToken };
}

afterEach(() => vi.unstubAllGlobals());

describe("a user without the Dashboard.View role", () => {
  it("is denied, and no request goes to the API", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    expect(await checkAccess(authWith(token({ roles: ["Chat.User"] })))).toBe(false);
    expect(await checkAccess(authWith("not-a-token"))).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("sees a plain notice", () => {
    expect(renderToStaticMarkup(<NoRole />)).toContain("You need the Dashboard.View role");
  });
});

describe("a user with the Dashboard.View role", () => {
  it("is let in", async () => {
    expect(await checkAccess(authWith(token({ roles: ["Dashboard.View"] })))).toBe(true);
  });

  it("is denied when no token can be had", async () => {
    const auth = { ...authWith(""), getToken: async () => Promise.reject(new Error("no")) };
    expect(await checkAccess(auth)).toBe(false);
  });
});
