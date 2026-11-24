import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  attachedReducer,
  attachPhoto,
  dropped,
  fitWithin,
  PhotoNotSent,
  shrink,
  UNREADABLE,
  uploadPhoto,
  withPhoto,
  type Attached,
  type AttachedAction,
  type UploadResult,
} from "./attachments";
import { buildChatRequest, httpError, streamChat } from "./chat";
import { Chat } from "./components/Chat";
import { showBlob } from "./components/PhotoButton";
import { chatReducer, initialChatState, type ChatState } from "./turns";
import type { ServerEvent } from "./types";

const blob = new Blob(["jpeg"], { type: "image/jpeg" });
const file = new File(["bytes"], "IMG_0001.HEIC");

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const answered = () =>
  new Response('event: delta\ndata: {"text":"Yes."}\n\nevent: done\ndata: {"conversation_id":"c","turn_id":"t","backend":"loop"}\n\n', {
    status: 200,
  });
const unknown = () => json({ detail: { code: "unknown_attachment" } }, 422);

async function collect(events: AsyncGenerator<ServerEvent>): Promise<ServerEvent[]> {
  const out: ServerEvent[] = [];
  for await (const event of events) out.push(event);
  return out;
}

function attached(...actions: AttachedAction[]): Attached {
  return actions.reduce(attachedReducer, {} as Attached);
}

const ready = (id = "a1", key = 1): AttachedAction[] => [
  { type: "chosen", key },
  { type: "uploading", key, blob },
  { type: "uploaded", key, result: { status: "ready", id } },
];

afterEach(() => vi.unstubAllGlobals());

describe("fitting a photo", () => {
  it.each([
    [4000, 3000, 1600, 1200],
    [3000, 4000, 1200, 1600],
    [1600, 900, 1600, 900],
    [640, 480, 640, 480],
    [5000, 1, 1600, 1],
  ])("%i x %i becomes %i x %i", (width, height, w, h) => {
    expect(fitWithin(width, height, 1600)).toEqual({ width: w, height: h });
  });
});

describe("shrinking a photo", () => {
  const canvas = { width: 0, height: 0, getContext: vi.fn(), toBlob: vi.fn() };
  const context = { fillStyle: "", fillRect: vi.fn(), drawImage: vi.fn() };

  function browser(bitmap: () => Promise<unknown>, out: Blob | null = blob) {
    canvas.getContext.mockReturnValue(context);
    canvas.toBlob.mockImplementation((done: (b: Blob | null) => void) => done(out));
    vi.stubGlobal("createImageBitmap", vi.fn(bitmap));
    vi.stubGlobal("document", { createElement: () => canvas });
  }

  it("draws it at most 1,600 px, as a JPEG", async () => {
    const bitmap = { width: 4000, height: 3000, close: vi.fn() };
    browser(async () => bitmap);
    expect(await shrink(file)).toBe(blob);
    expect([canvas.width, canvas.height]).toEqual([1600, 1200]);
    expect(context.drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 1600, 1200);
    expect(canvas.toBlob).toHaveBeenCalledWith(expect.any(Function), "image/jpeg", expect.any(Number));
    expect(bitmap.close).toHaveBeenCalled();
  });

  it("says what to do about a file the browser cannot decode", async () => {
    browser(() => Promise.reject(new DOMException("no", "InvalidStateError")));
    await expect(shrink(file)).rejects.toThrow("This file is not a photo the browser can read. Use JPEG, PNG or WebP.");
    browser(async () => ({ width: 10, height: 10, close: () => {} }), null);
    await expect(shrink(file)).rejects.toThrow(UNREADABLE);
  });
});

describe("choosing a photo", () => {
  function choose(shrinkImpl: () => Promise<Blob>, isCurrent = () => true) {
    const actions: AttachedAction[] = [];
    const upload = vi.fn(async () => {});
    return { actions, upload, done: attachPhoto({ file }, 1, { dispatch: (a) => actions.push(a), isCurrent, upload }, shrinkImpl) };
  }

  it("uploads it at once", async () => {
    const { upload, done } = choose(async () => blob);
    await done;
    expect(upload).toHaveBeenCalledWith({ key: 1, blob });
  });

  it("sends nothing when it cannot be read", async () => {
    const { actions, upload, done } = choose(() => Promise.reject(new Error("no")));
    await done;
    expect(upload).not.toHaveBeenCalled();
    expect(attached(...actions)).toEqual({ notice: UNREADABLE });
  });

  it("sends nothing when it was removed meanwhile", async () => {
    const { upload, done } = choose(async () => blob, () => false);
    await done;
    expect(upload).not.toHaveBeenCalled();
  });
});

describe("dropping files", () => {
  const picture = new File(["x"], "a.jpg", { type: "image/jpeg" });
  const other = new File(["x"], "b.jpg", { type: "image/jpeg" });
  const text = new File(["x"], "c.txt", { type: "text/plain" });

  it("takes one, and says so when there are more", () => {
    expect(dropped([])).toBeUndefined();
    expect(dropped([picture])).toEqual({ file: picture });
    expect(dropped([picture, other])).toEqual({ file: picture, note: "One photo was taken. You can attach one at a time." });
  });

  it("takes a picture before anything else", () => {
    expect(dropped([text, picture])!.file).toBe(picture);
    expect(dropped([text])).toEqual({ file: text });
  });
});

describe("the upload", () => {
  const upload = (fetchImpl: unknown) =>
    uploadPhoto({ apiUrl: "https://api/", token: "tok", blob, label: "Photo" }, fetchImpl as typeof fetch);

  it("posts the photo and its label, signed in", async () => {
    const fetchImpl = vi.fn(async (_url: string, _init: RequestInit) =>
      json({ id: "a1", kind: "photo", status: "ready", expires_at: "x" }, 201),
    );
    expect(await upload(fetchImpl)).toEqual({ status: "ready", id: "a1" });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("https://api/attachments");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" }); // the browser sets the content type of the form
    const form = init.body as FormData;
    expect(form.get("label")).toBe("Photo");
    expect((form.get("file") as File).size).toBe(blob.size);
    expect((form.get("file") as File).name).toBe("photo.jpg");
  });

  it("passes on the server's message for a rejected photo", async () => {
    const body = { id: null, kind: "photo", status: "rejected", code: "too_blurry", message: "The photo is blurred." };
    expect(await upload(async () => json(body, 201))).toEqual({ status: "rejected", message: "The photo is blurred." });
  });

  it.each([
    [413, "rejected", "The photo is too large. Choose a smaller one."],
    [415, "rejected", "This kind of file cannot be attached. Use JPEG, PNG or WebP."],
    [408, "error", "The upload took too long. Check your connection and try again."],
    [503, "error", "The service is busy with other photos. Wait a moment and try again."],
    [401, "error", "Your sign-in is no longer valid. Sign in again to attach a photo."],
    [403, "error", "Your account is not allowed to attach photos."],
    [500, "error", "The photo could not be uploaded. Try again."],
  ])("explains HTTP %i", async (status, kind, message) => {
    expect(await upload(async () => json({ detail: { code: "busy" } }, status))).toEqual({ status: kind, message });
  });

  it("explains a lost connection and an answer it does not know", async () => {
    const offline = async () => {
      throw new TypeError("failed");
    };
    expect(await upload(offline)).toEqual({
      status: "error",
      message: "The photo could not be uploaded. Check your connection and try again.",
    });
    expect(await upload(async () => json({ id: 7, status: "ready" }, 201))).toMatchObject({ status: "error" });
    expect(await upload(async () => new Response("<html>", { status: 201 }))).toMatchObject({ status: "error" });
  });
});

describe("the attached photo", () => {
  it("is ready once it is uploaded", () => {
    expect(attached({ type: "chosen", key: 1 })).toEqual({ photo: { key: 1, status: "preparing" } });
    expect(attached(...ready())).toEqual({ photo: { key: 1, status: "ready", blob, id: "a1" } });
  });

  it("is gone when it is refused, with the reason", () => {
    const result: UploadResult = { status: "rejected", message: "Too small." };
    expect(attached(...ready(), { type: "uploaded", key: 1, result })).toEqual({ notice: "Too small." });
  });

  it("stays, to try again, when the upload failed", () => {
    const result: UploadResult = { status: "error", message: "Busy." };
    const failed = attached({ type: "chosen", key: 1 }, { type: "uploading", key: 1, blob }, { type: "uploaded", key: 1, result });
    expect(failed).toEqual({ photo: { key: 1, status: "failed", blob }, notice: "Busy." });
    expect(attachedReducer(failed, { type: "uploading", key: 1, blob })).toEqual({ photo: { key: 1, status: "uploading", blob } });
  });

  it("is not touched by the result of another photo", () => {
    const second = attached(...ready("a1", 1), { type: "chosen", key: 2 });
    expect(attachedReducer(second, { type: "uploaded", key: 1, result: { status: "ready", id: "a9" } })).toBe(second);
    expect(attachedReducer({}, { type: "uploading", key: 1, blob })).toEqual({});
  });

  it("is removed by the customer and by New chat", () => {
    expect(attached(...ready(), { type: "remove" })).toEqual({});
    expect(attached(...ready(), { type: "reset" })).toEqual({});
  });
});

describe("a turn with a photo", () => {
  const photo = (state: Attached) => state.photo;
  const never = () => {
    throw new Error("It was not lost twice.");
  };

  function chat(fetchImpl: unknown) {
    const base = { apiUrl: "https://api", token: "t", backend: "loop" as const, message: "Is this OK?", correlationId: "x" };
    return (attachments: string[]) => streamChat({ ...base, attachments }, undefined, fetchImpl as typeof fetch);
  }

  const named = (fetchImpl: { mock: { calls: unknown[][] } }) =>
    fetchImpl.mock.calls.map(([, init]) => JSON.parse((init as RequestInit).body as string).attachments);

  it("names the photo on every message until it is removed", async () => {
    const fetchImpl = vi.fn(async () => answered());
    const again = vi.fn();
    let state = attached(...ready());
    await collect(withPhoto(photo(state), chat(fetchImpl), again, never));
    await collect(withPhoto(photo(state), chat(fetchImpl), again, never));
    state = attachedReducer(state, { type: "remove" });
    await collect(withPhoto(photo(state), chat(fetchImpl), again, never));
    expect(named(fetchImpl)).toEqual([["a1"], ["a1"], undefined]);
    expect(again).not.toHaveBeenCalled();
  });

  it("uploads the photo again by itself when the API has lost it", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(unknown()).mockResolvedValueOnce(answered());
    const again = vi.fn(async (): Promise<UploadResult> => ({ status: "ready", id: "a2" }));
    const events = await collect(withPhoto(photo(attached(...ready())), chat(fetchImpl), again, never));
    expect(events.map((e) => e.type)).toEqual(["delta", "done"]);
    expect(again).toHaveBeenCalledTimes(1);
    expect(again).toHaveBeenCalledWith({ key: 1, blob });
    expect(named(fetchImpl)).toEqual([["a1"], ["a2"]]);
  });

  it("is not sent when the API has lost it a second time, and the text goes back to the field", async () => {
    const fetchImpl = vi.fn(async () => unknown());
    const again = vi.fn(async (): Promise<UploadResult> => ({ status: "ready", id: "a2" }));
    const lost = vi.fn();
    const turn = withPhoto(photo(attached(...ready())), chat(fetchImpl), again, lost);
    await expect(collect(turn)).rejects.toBeInstanceOf(PhotoNotSent);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(again).toHaveBeenCalledTimes(1);
    expect(lost).toHaveBeenCalledTimes(1);
    expect(lost).toHaveBeenCalledWith(1);
  });

  it("keeps the photo, to try again, when the API has lost it twice", () => {
    const state = attached(...ready("a2"), { type: "lost", key: 1 });
    const notice = "The photo is no longer available. Try again, or remove it and ask without it.";
    expect(state).toEqual({ photo: { key: 1, status: "failed", blob }, notice });
    expect(render(initialChatState, state)).toContain("Try again</button>");
    expect(render(initialChatState, state)).toContain(notice);
    expect(attached(...ready(), { type: "lost", key: 2 }).photo!.status).toBe("ready");
  });

  it("uploads again for no other failure", async () => {
    const fetchImpl = vi.fn(async () => json({ detail: "turn_in_progress" }, 409));
    const again = vi.fn();
    const events = await collect(withPhoto(photo(attached(...ready())), chat(fetchImpl), again, never));
    expect(events).toMatchObject([{ type: "error", data: { code: "http_409" } }]);
    expect(again).not.toHaveBeenCalled();
  });

  it("is not sent when the new upload is refused: the text goes back to the field", async () => {
    const message = "The photo is blurred. Hold still or slow down and take a new one.";
    const again = async (): Promise<UploadResult> => ({ status: "rejected", message });
    const turn = withPhoto(photo(attached(...ready())), chat(async () => unknown()), again, never);
    await expect(collect(turn)).rejects.toBeInstanceOf(PhotoNotSent);

    let state = chatReducer(initialChatState, { type: "draft", text: "Is this OK?" });
    state = chatReducer(state, { type: "start", message: "Is this OK?", backend: "loop", correlationId: "x", photo: blob });
    expect(state.draft).toBe("");
    state = chatReducer(state, { type: "withdraw" });
    expect(state.turns).toEqual([]);
    const refused = attached(...ready(), { type: "uploaded", key: 1, result: { status: "rejected", message } });
    const html = render(state, refused);
    expect(html).toMatch(/<textarea[^>]*>Is this OK\?<\/textarea>/);
    expect(html).toMatch(new RegExp(`role="alert"[^>]*>${message}`));
  });
});

describe("the text that goes back", () => {
  it("does not replace what was written meanwhile", () => {
    let state = chatReducer(initialChatState, { type: "start", message: "First", backend: "loop", correlationId: "x" });
    state = chatReducer(state, { type: "draft", text: "Second" });
    expect(chatReducer(state, { type: "withdraw" }).draft).toBe("First\nSecond");
  });

  it("is not lost to a suggested question", () => {
    const state = chatReducer(initialChatState, { type: "draft", text: "Half a" });
    const next = chatReducer(state, { type: "start", message: "A suggestion", backend: "loop", correlationId: "x" });
    expect(next.draft).toBe("Half a");
  });
});

describe("HTTP errors of a turn", () => {
  it("keep the API's code", () => {
    expect(httpError(422, "unknown_attachment")).toMatchObject({
      type: "error",
      data: { code: "unknown_attachment", message: expect.stringContaining("photo") },
    });
    expect(httpError(422)).toMatchObject({ type: "error", data: { code: "http_422" } });
  });

  it("survive a body that is not JSON", async () => {
    const request = { apiUrl: "https://api", token: "t", backend: "loop" as const, message: "hi", correlationId: "x" };
    const fetchImpl = (async () => new Response("<html>", { status: 502 })) as unknown as typeof fetch;
    expect(await collect(streamChat(request, undefined, fetchImpl))).toMatchObject([{ data: { code: "http_502" } }]);
    expect(buildChatRequest(request).url).toBe("https://api/chat");
  });
});

function render(state: ChatState, photo: Attached = {}, busy = false): string {
  return renderToStaticMarkup(
    createElement(Chat, {
      state,
      busy,
      underTheHood: false,
      photo: { attached: photo, choose: () => {}, retry: () => {}, remove: () => {} },
      onDraft: () => {},
      onSend: () => {},
      onOpenSource: () => {},
    }),
  );
}

describe("the chat", () => {
  const sendButton = (html: string) => html.match(/<button class="primary"[^>]*>/)![0];
  const drafted = chatReducer(initialChatState, { type: "draft", text: "Is this OK?" });

  // The URL comes after the first render, and a picture without one would load the page itself.
  it("has the photo's thumbnail in the user's bubble, never as a picture without an address", () => {
    const state = chatReducer(initialChatState, {
      type: "start",
      message: "Is this OK?",
      backend: "loop",
      correlationId: "x",
      photo: blob,
    });
    expect(state.turns[0]!.photo).toBe(blob);
    expect(render(state)).toContain('<div class="bubble user"><span class="photo-thumb" role="img" aria-label="Your photo"></span>Is this OK?</div>');
    expect(render(state, attached(...ready()))).not.toMatch(/<img(?![^>]* src=)/);
  });

  it("has a named button for a photo, and says that photos are not stored", () => {
    const html = render(initialChatState);
    expect(html).toContain('aria-label="Attach a photo"');
    expect(html).toContain('accept="image/jpeg,image/png,image/webp"');
    expect(html).toContain("with contact details masked. The live screen keeps nothing, but your chat is kept by the service.");
    expect(html).toContain("Photos are not stored in the conversation.");
    expect(html).not.toContain("photo-preview");
  });

  it("shows the photo with its remove button and the note beside it", () => {
    const preview = render(drafted, attached(...ready())).match(/<div class="photo-preview".*?<\/div>/)![0];
    expect(preview).toContain('aria-label="Remove the photo"');
    expect(preview).toContain("Photos are not stored in the conversation.");
  });

  it("says once that photos are not stored, with and without a photo", () => {
    for (const state of [{}, attached(...ready())])
      expect(render(drafted, state).match(/Photos are not stored in the conversation\./g)).toHaveLength(1);
  });

  it("does not let the photo be changed while an answer is on its way", () => {
    const button = (html: string, label: string) => html.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`))![0];
    for (const label of ["Attach a photo", "Remove the photo"]) {
      expect(button(render(drafted, attached(...ready())), label)).not.toContain("disabled");
      expect(button(render(drafted, attached(...ready()), true), label)).toContain("disabled");
    }
    const failed = attached(...ready(), { type: "lost", key: 1 });
    expect(render(drafted, failed, true)).toMatch(/<button[^>]*disabled=""[^>]*>Try again<\/button>/);
  });

  it("says that one photo of several was taken", () => {
    const note = "One photo was taken. You can attach one at a time.";
    const state = attached({ type: "chosen", key: 1, note }, { type: "uploading", key: 1, blob }, { type: "uploaded", key: 1, result: { status: "ready", id: "a" } });
    expect(render(drafted, state)).toContain(note);
    expect(attachedReducer(state, { type: "chosen", key: 2 })).toEqual({ photo: { key: 2, status: "preparing" } });
  });

  it("waits with sending until the photo is uploaded", () => {
    const chosen = attached({ type: "chosen", key: 1 });
    const failed = attached(
      { type: "chosen", key: 1 },
      { type: "uploading", key: 1, blob },
      { type: "uploaded", key: 1, result: { status: "error", message: "Busy." } },
    );
    expect(sendButton(render(drafted, chosen))).toContain("disabled");
    expect(sendButton(render(drafted, failed))).toContain("disabled");
    expect(render(drafted, failed)).toContain("Try again");
    expect(sendButton(render(drafted, attached(...ready())))).not.toContain("disabled");
    expect(sendButton(render(drafted))).not.toContain("disabled");
  });
});

describe("a thumbnail's URL", () => {
  it("is revoked when it is no longer shown", () => {
    const urls = { createObjectURL: vi.fn(() => "blob:1"), revokeObjectURL: vi.fn() };
    vi.stubGlobal("URL", urls);
    const show = vi.fn();
    const hide = showBlob(blob, show);
    expect(show).toHaveBeenCalledWith("blob:1");
    expect(urls.revokeObjectURL).not.toHaveBeenCalled();
    hide();
    expect(urls.revokeObjectURL).toHaveBeenCalledWith("blob:1");
  });
});
