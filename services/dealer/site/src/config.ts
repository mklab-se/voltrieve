export interface AppConfig {
  tenantId: string;
  clientId: string;
  scope: string;
  /** Dev-only mock sign-in; always false in production builds. */
  fakeAuth: boolean;
}

export type ConfigResult = { ok: true; config: AppConfig } | { ok: false; missing: string[] };

const REQUIRED = ["tenantId", "clientId", "scope"] as const;

/** The service that serves the page gives its settings, so one build fits every deployment. No secrets. */
export async function loadConfig(fakeAuth: boolean): Promise<ConfigResult> {
  let raw: Record<string, unknown> = {};
  try {
    const response = await fetch("/config.json", { cache: "no-store" });
    if (response.ok) raw = { ...((await response.json()) as object | null) };
  } catch {
    /* every setting is missing then */
  }
  const read = (key: string) => (typeof raw[key] === "string" ? (raw[key] as string).trim() : "");
  const missing = REQUIRED.filter((key) => !read(key));
  if (missing.length > 0) return { ok: false, missing };
  return { ok: true, config: { tenantId: read("tenantId"), clientId: read("clientId"), scope: read("scope"), fakeAuth } };
}
