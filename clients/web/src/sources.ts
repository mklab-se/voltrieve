import type { SourceItem } from "./types";

const HREF_PREFIX = "#source=";
// A signed link is only ever to a blob in Azure Storage.
const BLOB_URL = /^https:\/\/[a-z0-9]+\.blob\.core\.windows\.net\//;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Make each cited title of a linkable source a markdown link the answer renders as a button. */
export function linkCitations(text: string, sources: SourceItem[] | undefined): string {
  let out = text;
  for (const s of sources ?? []) {
    if (s.kind !== "source" || !s.link) continue;
    const title = new RegExp(`(?<![\\w./:\\[-])${escapeRegExp(s.id)}(?![\\w-])`, "g");
    out = out.replace(title, `[${s.id}](${HREF_PREFIX}${encodeURIComponent(s.id)})`);
  }
  return out;
}

export function sourceFromHref(href: string | undefined): string | undefined {
  return href?.startsWith(HREF_PREFIX) ? decodeURIComponent(href.slice(HREF_PREFIX.length)) : undefined;
}

/** Ask the API for a short signed link to one document. Each click gets a fresh one. */
export async function fetchSourceUrl(apiUrl: string, token: string, link: string, fetchImpl: typeof fetch = fetch): Promise<string> {
  const response = await fetchImpl(`${apiUrl.replace(/\/+$/, "")}${link}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
  });
  if (response.status === 403 || response.status === 404) throw new Error("This document cannot be opened.");
  if (!response.ok) throw new Error(`The document could not be opened (${response.status}).`);
  const { url } = (await response.json()) as { url?: unknown };
  if (typeof url !== "string" || !BLOB_URL.test(url)) throw new Error("The API answered with an unexpected link.");
  return url;
}

/** Open the window during the click, so no popup blocker stops it, then send it to the link. */
export async function openSource(getUrl: () => Promise<string>, open: typeof window.open = window.open.bind(window)): Promise<void> {
  const win = open("", "_blank");
  if (win) win.opener = null;
  try {
    const url = await getUrl();
    if (win) win.location.href = url;
    else open(url, "_blank", "noopener");
  } catch (e) {
    win?.close();
    throw e;
  }
}
