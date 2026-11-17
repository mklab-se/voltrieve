import type { SourceItem } from "./types";

const HREF_PREFIX = "#source=";
// A signed link is only ever to a blob in Azure Storage.
const BLOB_URL = /^https:\/\/[a-z0-9]+\.blob\.core\.windows\.net\//;

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Make each cited title of a linkable source a markdown link the answer renders as a button.
 * Same boundaries as the API (api/app/sources.py): not inside a URL, a path or a longer name.
 * Code spans and code blocks are left as they are. */
export function linkCitations(text: string, sources: SourceItem[] | undefined): string {
  const linkable = (sources ?? []).filter((s) => s.kind === "source" && s.link);
  if (linkable.length === 0) return text;
  // Odd pieces are code: a run of backticks, anything but a backtick, the same run again.
  const pieces = text.split(/(`+[^`]*`+)/);
  return pieces
    .map((piece, i) => {
      if (i % 2 === 1) return piece;
      let out = piece;
      for (const s of linkable) {
        const title = new RegExp(`(?<![\\w./:=\\[\`-])${escapeRegExp(s.id)}(?![\\w\`-]|\\.\\w)`, "g");
        out = out.replace(title, `[${s.id}](${HREF_PREFIX}${encodeURIComponent(s.id)})`);
      }
      return out;
    })
    .join("");
}

export function sourceFromHref(href: string | undefined): string | undefined {
  if (!href?.startsWith(HREF_PREFIX)) return undefined;
  try {
    return decodeURIComponent(href.slice(HREF_PREFIX.length));
  } catch {
    return undefined; // model text can hold any href: a bad one is just not a source
  }
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
