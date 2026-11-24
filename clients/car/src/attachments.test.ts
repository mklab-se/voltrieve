import { afterEach, describe, expect, it, vi } from "vitest";
import { SignPhotos, uploadPhoto, type UploadResult } from "./attachments";

const blob = (text = "jpeg") => new Blob([text], { type: "image/jpeg" });
const input = { apiUrl: "https://api.example/", token: "tok", blob: blob(), label: "Parking sign in block 3" };

const answering = (status: number, body: unknown) =>
  vi.fn(async (_url: string, _init: RequestInit) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status }));

const upload = (fetchImpl: ReturnType<typeof answering>) => uploadPhoto(input, fetchImpl as unknown as typeof fetch);

describe("uploadPhoto", () => {
  it("sends the photo and its label, and gives the id", async () => {
    const fetchImpl = answering(201, { id: "att1", kind: "photo", status: "ready", expires_at: "2026-11-24T10:00:00Z" });
    expect(await upload(fetchImpl)).toEqual({ status: "ready", id: "att1" });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("https://api.example/attachments");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ Authorization: "Bearer tok", Accept: "application/json" }); // the browser adds the form's type
    const form = init.body as FormData;
    expect([...form.keys()]).toEqual(["file", "label"]);
    expect(await (form.get("file") as File).text()).toBe("jpeg");
    expect(form.get("label")).toBe("Parking sign in block 3");
  });

  it("gives the server's message when the photo is rejected", async () => {
    const message = "The photo is blurred. Hold still or slow down and take a new one.";
    const rejected = answering(201, { id: null, kind: "photo", status: "rejected", code: "too_blurry", message });
    expect(await upload(rejected)).toEqual({ status: "rejected", message });
  });

  it.each([
    [408, { detail: "The upload took too long" }, /took too long/],
    [413, { detail: "The attachment is too large" }, /too large/],
    [415, { detail: "Unknown kind of attachment" }, /cannot be read/],
    [503, { detail: { code: "busy" } }, /busy/],
    [401, { detail: "x" }, /sign-in/],
    [403, { detail: "Chat.User" }, /not allowed/],
    [500, "not json", /could not be sent/],
    [201, "not json", /could not be sent/],
    [201, { status: "ready", id: 7 }, /could not be sent/],
    [201, { status: "rejected" }, /could not be sent/],
    [201, null, /could not be sent/],
  ])("gives %i %j a clear error in plain words", async (status, body, text) => {
    const result = await upload(answering(status as number, body));
    expect(result.status).toBe("error");
    const { message } = result as { message: string };
    expect(message).toMatch(text as RegExp);
    expect(message).not.toMatch(/API|HTTP|Chat\.User|backend|[0-9]/);
  });

  it("gives an error when the API cannot be reached", async () => {
    const down = vi.fn(async () => {
      throw new TypeError("failed");
    });
    expect(await upload(down as never)).toEqual({ status: "error", message: "The photo could not be sent. Check the connection and try again." });
  });
});

describe("SignPhotos", () => {
  let next = 0;
  const ready = () =>
    vi.fn(async (_photo: { block: number; blob: Blob }): Promise<UploadResult> => ({ status: "ready", id: `att${(next += 1)}` }));

  afterEach(() => {
    next = 0;
    vi.restoreAllMocks();
  });

  it("has no photo before the first sign", async () => {
    const photos = new SignPhotos();
    expect(photos.last()).toBeUndefined();
    const up = ready();
    expect(await photos.idForLast(up)).toEqual({ status: "none", message: "No parking sign has been passed yet." });
    expect(up).not.toHaveBeenCalled();
  });

  it("keeps the last photo, and lets go of the picture of the one before", () => {
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    const photos = new SignPhotos();
    const first = blob("one");
    photos.add(0, first);
    expect(photos.last()).toMatchObject({ block: 0, blob: first });
    const { url } = photos.last()!;
    expect(url).toMatch(/^blob:/);
    expect(photos.last()!.url).toBe(url);
    photos.add(1, blob("two"));
    expect(photos.last()!.block).toBe(1);
    expect(revoke).toHaveBeenCalledExactlyOnceWith(url);
  });

  it("uploads a photo once for two questions, also when they come at the same time", async () => {
    const photos = new SignPhotos();
    photos.add(3, blob());
    const up = ready();
    const both = await Promise.all([photos.idForLast(up), photos.idForLast(up)]);
    expect(both).toEqual([{ status: "ready", id: "att1", block: 3 }, { status: "ready", id: "att1", block: 3 }]);
    expect(await photos.idForLast(up)).toMatchObject({ id: "att1" });
    expect(up).toHaveBeenCalledTimes(1);
    expect(up.mock.calls[0]![0]).toMatchObject({ block: 3 });
    photos.add(4, blob());
    expect(await photos.idForLast(up)).toMatchObject({ id: "att2", block: 4 });
  });

  it("asks once about a rejected photo, and tries again after an error", async () => {
    const photos = new SignPhotos();
    photos.add(0, blob());
    const up = vi.fn(async (): Promise<UploadResult> => ({ status: "error", message: "down" }));
    expect(await photos.idForLast(up)).toEqual({ status: "error", message: "down", block: 0 });
    up.mockResolvedValue({ status: "rejected", message: "blurred" });
    expect(await photos.idForLast(up)).toMatchObject({ status: "rejected", message: "blurred" });
    expect(await photos.idForLast(up)).toMatchObject({ status: "rejected" });
    expect(up).toHaveBeenCalledTimes(2);
  });

  it("tries again after an upload that throws", async () => {
    const photos = new SignPhotos();
    photos.add(0, blob());
    await expect(photos.idForLast(() => Promise.reject(new Error("Not signed in.")))).rejects.toThrow("Not signed in.");
    expect(await photos.idForLast(ready())).toMatchObject({ status: "ready" });
  });

  it("uploads the same photo again when the API has lost it, also after the next sign", async () => {
    const photos = new SignPhotos();
    const up = ready();
    const asked = blob("asked about");
    photos.add(3, asked);
    await photos.idForLast(up);
    photos.add(4, blob("the next one"));
    expect(await photos.renew(["att1"], up)).toEqual({ ids: ["att2"] });
    expect(up.mock.calls[1]![0]).toMatchObject({ block: 3, blob: asked });
    expect(await photos.renew(["att2"], up)).toEqual({ ids: ["att3"] }); // the new id is known too
  });

  it("uses the new id for the next question", async () => {
    const photos = new SignPhotos();
    const up = ready();
    photos.add(3, blob());
    await photos.idForLast(up);
    await photos.renew(["att1"], up);
    expect(await photos.idForLast(up)).toMatchObject({ id: "att2" });
    expect(up).toHaveBeenCalledTimes(2);
  });

  it("says why when it cannot renew", async () => {
    const photos = new SignPhotos();
    const up = ready();
    expect(await photos.renew(["other"], up)).toEqual({ message: "The photo is gone. Ask again." });
    photos.add(0, blob());
    await photos.idForLast(up);
    up.mockResolvedValue({ status: "error", message: "busy" });
    expect(await photos.renew(["att1"], up)).toEqual({ message: "busy" });
  });

  it("does not keep every photo of a long drive", async () => {
    const photos = new SignPhotos();
    const up = ready();
    for (let block = 0; block < 20; block += 1) {
      photos.add(block, blob());
      await photos.idForLast(up);
    }
    expect(await photos.renew(["att20"], up)).toHaveProperty("ids");
    expect(await photos.renew(["att1"], up)).toHaveProperty("message");
  });
});
