import { afterEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "./config";

const CONFIG = { tenantId: "t", clientId: "c", scope: "api://x/manage_as_user" };
afterEach(() => vi.unstubAllGlobals());

const answers = (body: string, status = 200) => vi.stubGlobal("fetch", async () => new Response(body, { status }));

describe("the configuration", () => {
  it("comes from the service", async () => {
    answers(JSON.stringify(CONFIG));
    expect(await loadConfig(false)).toEqual({ ok: true, config: { ...CONFIG, fakeAuth: false } });
  });

  it("names what is missing", async () => {
    answers(JSON.stringify({ tenantId: " ", clientId: "c" }));
    expect(await loadConfig(false)).toEqual({ ok: false, missing: ["tenantId", "scope"] });
  });

  it("is missing when the service does not give it", async () => {
    for (const [body, status] of [["", 503], ["<!doctype html>", 200], ["null", 200]] as const) {
      answers(body, status);
      expect(await loadConfig(false)).toMatchObject({ ok: false });
    }
    vi.stubGlobal("fetch", async () => Promise.reject(new TypeError("no")));
    expect(await loadConfig(false)).toMatchObject({ ok: false });
  });

  it("has the fake sign-in only when it is asked for", async () => {
    answers(JSON.stringify(CONFIG));
    expect(await loadConfig(true)).toMatchObject({ config: { fakeAuth: true } });
  });
});
