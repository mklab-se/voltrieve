import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
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

it("shows the chat and nothing of what is under the hood", () => {
  const html = renderToStaticMarkup(<Workspace config={config} auth={auth} session={{ name: "Dev", username: "dev@x" }} />);
  expect(html).toContain("Ask Runa something");
  const text = html.replace(/<[^>]*>/g, " ");
  expect(text).not.toMatch(/\b(backend|loop|prompt|hosted|trace|event)/i);
});
