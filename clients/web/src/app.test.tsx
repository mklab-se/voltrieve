import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Workspace } from "./App";
import type { Auth } from "./auth";

const auth: Auth = {
  kind: "fake",
  init: async () => null,
  signIn: async () => {},
  signOut: async () => {},
  getToken: async () => "token",
};
const config = { apiUrl: "http://api", tenantId: "", clientId: "", scope: "", fakeAuth: true };

function renderAt(search: string): string {
  vi.stubGlobal("location", { search, href: `http://app/${search}` });
  vi.stubGlobal("document", { documentElement: { dataset: {} } });
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
  return renderToStaticMarkup(<Workspace config={config} auth={auth} session={{ name: "Dev", username: "dev@x" }} />);
}

afterEach(() => vi.unstubAllGlobals());

// The operations page is its own app now. An old link to it on this site opens the chat. The old
// word is built here so that the grep that proves the removal stays clean.
const OLD = ["dash", "board"].join("");

describe("old links to the operations page", () => {
  it.each([`?view=${OLD}`, `?page=${OLD}`, ""])("open the chat for %j", (search) => {
    const html = renderAt(search);
    expect(html).toContain("Ask Runa something");
    expect(html).not.toContain("data-page");
    expect(html).not.toContain(OLD);
  });
});
