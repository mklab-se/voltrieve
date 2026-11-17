import { describe, expect, it, vi } from "vitest";
import { parseServerEvent } from "./events";
import { fetchSourceUrl, linkCitations, openSource, sourceFromHref } from "./sources";
import { chatReducer, initialChatState } from "./turns";
import type { SourceItem } from "./types";

const BROCHURE: SourceItem = {
  id: "voltrieve-fjord-brochure.pdf",
  title: "Fjord brochure",
  kind: "source",
  link: "/sources/voltrieve-fjord-brochure.pdf",
};
const CATALOG: SourceItem = { id: "voltrieve-parts-brakes.md", title: "Parts brakes", kind: "source" };
const SPECS: SourceItem = {
  id: "voltrieve-fjord-specifications.pdf",
  title: "Fjord specifications",
  kind: "read_more",
  link: "/sources/voltrieve-fjord-specifications.pdf",
};

describe("the sources event", () => {
  it("keeps valid items", () => {
    const ev = parseServerEvent({ event: "sources", data: JSON.stringify({ turn_id: "t", items: [BROCHURE, CATALOG, SPECS] }) });
    expect(ev).toEqual({ type: "sources", data: { items: [BROCHURE, CATALOG, SPECS] } });
  });

  it("drops links that do not point at the API's sources path", () => {
    const evil = { ...BROCHURE, link: "https://evil.example/x.pdf" };
    const sneaky = { ...BROCHURE, link: "//evil.example/sources/x" };
    const ev = parseServerEvent({ event: "sources", data: JSON.stringify({ items: [evil, sneaky] }) });
    expect(ev).toEqual({
      type: "sources",
      data: { items: [{ ...BROCHURE, link: undefined }, { ...BROCHURE, link: undefined }] },
    });
  });

  it("drops items with an unknown kind or no id", () => {
    const ev = parseServerEvent({
      event: "sources",
      data: JSON.stringify({ items: [{ ...BROCHURE, kind: "other" }, { title: "x", kind: "source" }, CATALOG] }),
    });
    expect(ev).toEqual({ type: "sources", data: { items: [CATALOG] } });
  });

  it("attaches the sources to the turn", () => {
    let state = chatReducer(initialChatState, { type: "start", message: "hi", backend: "loop", correlationId: "c" });
    state = chatReducer(state, { type: "event", event: { type: "sources", data: { items: [BROCHURE] } } });
    expect(state.turns[0]!.sources).toEqual([BROCHURE]);
  });
});

describe("linkCitations", () => {
  it("turns a cited title with a link into a markdown link", () => {
    const text = "Air suspension. (voltrieve-fjord-brochure.pdf)";
    expect(linkCitations(text, [BROCHURE])).toBe(
      "Air suspension. ([voltrieve-fjord-brochure.pdf](#source=voltrieve-fjord-brochure.pdf))",
    );
  });

  it("leaves titles without a link, and longer names, alone", () => {
    const text = "VP-10007 (voltrieve-parts-brakes.md), see old-voltrieve-fjord-brochure.pdf";
    expect(linkCitations(text, [BROCHURE, CATALOG])).toBe(text);
  });

  it("only links sources, not read more suggestions the model never cited", () => {
    const text = "WLTP (voltrieve-fjord-specifications.pdf)";
    expect(linkCitations(text, [SPECS])).toBe(text);
  });

  it("reads the id back from the href", () => {
    expect(sourceFromHref("#source=voltrieve-fjord-brochure.pdf")).toBe("voltrieve-fjord-brochure.pdf");
    expect(sourceFromHref("https://example.com")).toBeUndefined();
  });
});

describe("opening a source", () => {
  it("asks the API for a signed link with the user's token", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ url: "https://st.blob.core.windows.net/voltrieve-docs/a.pdf?sig=x", expires_in: 300 }),
    );
    const url = await fetchSourceUrl("https://api.example/", "tok", BROCHURE.link!, fetchMock);
    expect(url).toBe("https://st.blob.core.windows.net/voltrieve-docs/a.pdf?sig=x");
    expect(fetchMock).toHaveBeenCalledWith("https://api.example/sources/voltrieve-fjord-brochure.pdf", {
      headers: { Authorization: "Bearer tok", Accept: "application/json" },
    });
  });

  it("refuses a link that is not a blob in Azure Storage", async () => {
    const fetchMock = vi.fn(async () => Response.json({ url: "https://evil.example/a.pdf" }));
    await expect(fetchSourceUrl("https://api.example", "tok", BROCHURE.link!, fetchMock)).rejects.toThrow();
  });

  it("says why when the API refuses", async () => {
    const fetchMock = vi.fn(async () => new Response("", { status: 403 }));
    await expect(fetchSourceUrl("https://api.example", "tok", BROCHURE.link!, fetchMock)).rejects.toThrow(/cannot be opened/);
  });

  it("opens the window at once and points it at the document when the link arrives", async () => {
    const win = { location: { href: "" }, close: vi.fn(), opener: {} as unknown };
    const open = vi.fn(() => win as unknown as Window);
    await openSource(async () => "https://st.blob.core.windows.net/c/a.pdf?sig=x", open);
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(win.opener).toBeNull();
    expect(win.location.href).toBe("https://st.blob.core.windows.net/c/a.pdf?sig=x");
  });

  it("closes the window when the link cannot be had", async () => {
    const win = { location: { href: "" }, close: vi.fn(), opener: {} as unknown };
    await expect(
      openSource(async () => {
        throw new Error("no");
      }, () => win as unknown as Window),
    ).rejects.toThrow("no");
    expect(win.close).toHaveBeenCalled();
  });
});
