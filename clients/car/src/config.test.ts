// Copied from clients/web; keep the two alike. Only the car has remote control.
import { describe, expect, it } from "vitest";
import { readConfig } from "./config";

const full = {
  VITE_API_URL: "https://api",
  VITE_ENTRA_TENANT_ID: "t",
  VITE_ENTRA_CLIENT_ID: "c",
  VITE_API_SCOPE: "api://x/access_as_user",
};

describe("readConfig", () => {
  it("accepts a complete config", () => {
    expect(readConfig(full, false)).toMatchObject({ ok: true, config: { clientId: "c", fakeAuth: false } });
  });

  it("lists every missing variable", () => {
    expect(readConfig({ VITE_API_URL: "x", VITE_ENTRA_CLIENT_ID: " " }, false)).toEqual({
      ok: false,
      missing: ["VITE_ENTRA_TENANT_ID", "VITE_ENTRA_CLIENT_ID", "VITE_API_SCOPE"],
    });
  });

  it("allows fake auth with only the API url in dev", () => {
    expect(readConfig({ VITE_API_URL: "http://m", VITE_DEV_FAKE_AUTH: "true" }, true)).toMatchObject({
      ok: true,
      config: { fakeAuth: true },
    });
  });

  it("has remote control only when it is set to true", () => {
    const on = (VITE_REMOTE_CONTROL?: string) => {
      const result = readConfig({ ...full, VITE_REMOTE_CONTROL }, false);
      return result.ok && result.config.remoteControl;
    };
    expect([undefined, "", "false", "1", "TRUE", "true"].map(on)).toEqual([false, false, false, false, false, true]);
  });

  it("ignores fake auth in production", () => {
    expect(readConfig({ VITE_API_URL: "http://m", VITE_DEV_FAKE_AUTH: "true" }, false).ok).toBe(false);
  });
});
