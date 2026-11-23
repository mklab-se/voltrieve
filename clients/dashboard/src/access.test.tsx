import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Auth } from "./auth";
import { checkAccess } from "./dashboard";
import { Gate } from "./components/Gate";

function token(payload: object): string {
  const b64 = (o: object) => btoa(JSON.stringify(o)).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${b64({ alg: "RS256" })}.${b64(payload)}.sig`;
}

function authWith(accessToken: string): Auth {
  return { kind: "fake", init: async () => null, signIn: async () => {}, signOut: async () => {}, getToken: async () => accessToken };
}

describe("a user without the Dashboard.View role", () => {
  it("is denied by the token check", async () => {
    expect(await checkAccess(authWith(token({ roles: ["Chat.User"] })))).toBe(false);
    expect(await checkAccess(authWith("not-a-token"))).toBe(false);
  });
});

// The dashboard is what asks the API for the feed, so it must not exist unless access is allowed.
describe("the gate", () => {
  const show = (access: "checking" | "allowed" | "denied") =>
    renderToStaticMarkup(<Gate access={access} apiUrl="http://api" auth={authWith("t")} />);

  it("shows the notice and no dashboard when denied", () => {
    const html = show("denied");
    expect(html).toContain("You need the Dashboard.View role");
    expect(html).not.toContain("Operations dashboard");
  });

  it("shows neither while checking", () => {
    const html = show("checking");
    expect(html).not.toContain("You need the Dashboard.View role");
    expect(html).not.toContain("Operations dashboard");
  });

  it("shows the dashboard when allowed", () => {
    const html = show("allowed");
    expect(html).toContain("Operations dashboard");
    expect(html).not.toContain("You need the Dashboard.View role");
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
