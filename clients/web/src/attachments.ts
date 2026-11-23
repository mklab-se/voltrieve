import type { ServerEvent } from "./types";

export const UNREADABLE = "This file is not a photo the browser can read. Use JPEG, PNG or WebP.";
const MAX_SIDE = 1600;
const JPEG_QUALITY = 0.9;

/** The size that fits within a square of `maxSide`. Never larger than the photo is. */
export function fitWithin(width: number, height: number, maxSide: number): { width: number; height: number } {
  const scale = Math.min(1, maxSide / Math.max(width, height));
  const fit = (side: number) => Math.max(1, Math.round(side * scale));
  return { width: fit(width), height: fit(height) };
}

/** A smaller JPEG of the photo, upright and without its metadata. Throws UNREADABLE when it is not one. */
export async function shrink(file: File, maxSide = MAX_SIDE): Promise<Blob> {
  let bitmap: ImageBitmap | undefined;
  try {
    bitmap = await createImageBitmap(file);
    const { width, height } = fitWithin(bitmap.width, bitmap.height, maxSide);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d")!;
    context.fillStyle = "#fff"; // a JPEG has no transparency
    context.fillRect(0, 0, width, height);
    context.drawImage(bitmap, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, "image/jpeg", JPEG_QUALITY));
    if (blob) return blob;
  } catch {
    /* HEIC, a broken file, or not a picture at all */
  } finally {
    bitmap?.close();
  }
  throw new Error(UNREADABLE);
}

/** "rejected" means another photo is needed. "error" means that the same one may work later. */
export type UploadResult =
  | { status: "ready"; id: string }
  | { status: "rejected"; message: string }
  | { status: "error"; message: string };

export interface UploadInput {
  apiUrl: string;
  token: string;
  blob: Blob;
  label: string;
}

const REFUSED: Record<number, UploadResult> = {
  401: { status: "error", message: "Your sign-in is no longer valid. Sign in again to attach a photo." },
  403: { status: "error", message: "Your account is not allowed to attach photos." },
  408: { status: "error", message: "The upload took too long. Check your connection and try again." },
  413: { status: "rejected", message: "The photo is too large. Choose a smaller one." },
  415: { status: "rejected", message: "This kind of file cannot be attached. Use JPEG, PNG or WebP." },
  503: { status: "error", message: "The service is busy with other photos. Wait a moment and try again." },
};

const FAILED: UploadResult = { status: "error", message: "The photo could not be uploaded. Try again." };

/** POST /attachments. Never throws. */
export async function uploadPhoto(input: UploadInput, fetchImpl: typeof fetch = fetch): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", input.blob, "photo.jpg"); // not the customer's file name
  form.append("label", input.label);
  let res: Response;
  try {
    res = await fetchImpl(`${input.apiUrl.replace(/\/+$/, "")}/attachments`, {
      method: "POST",
      headers: { Authorization: `Bearer ${input.token}` },
      body: form,
    });
  } catch {
    return { status: "error", message: "The photo could not be uploaded. Check your connection and try again." };
  }
  if (!res.ok) return REFUSED[res.status] ?? FAILED;
  try {
    const body = (await res.json()) as { status?: unknown; id?: unknown; message?: unknown };
    if (body.status === "ready" && typeof body.id === "string" && body.id) return { status: "ready", id: body.id };
    if (body.status === "rejected" && typeof body.message === "string") return { status: "rejected", message: body.message };
  } catch {
    /* not JSON */
  }
  return FAILED;
}

/** The photo beside the text field. It stays until the customer removes it or it is refused. */
export interface AttachedPhoto {
  /** Tells a result for this photo from a late one for another. */
  key: number;
  status: "preparing" | "uploading" | "ready" | "failed";
  blob?: Blob;
  id?: string;
  /** What to know about how it was chosen. */
  note?: string;
}

export interface Attached {
  photo?: AttachedPhoto;
  /** Why the last photo was refused or could not be uploaded. */
  notice?: string;
}

export type AttachedAction =
  | { type: "chosen"; key: number; note?: string }
  | { type: "uploading"; key: number; blob: Blob }
  | { type: "uploaded"; key: number; result: UploadResult }
  /** The API has lost it, also after a new upload. */
  | { type: "lost"; key: number }
  | { type: "remove" }
  | { type: "reset" };

export function attachedReducer(state: Attached, action: AttachedAction): Attached {
  switch (action.type) {
    case "remove":
    case "reset":
      return {};
    case "chosen":
      return { photo: { key: action.key, status: "preparing", ...(action.note && { note: action.note }) } };
  }
  const { photo } = state;
  if (photo?.key !== action.key) return state;
  const { id: _, ...kept } = photo;
  if (action.type === "uploading") return { photo: { ...kept, status: "uploading", blob: action.blob } };
  if (action.type === "lost") return { photo: { ...kept, status: "failed" } };
  const { result } = action;
  if (result.status === "rejected") return { notice: result.message };
  if (result.status === "error") return { photo: { ...kept, status: "failed" }, notice: result.message };
  return { photo: { ...kept, status: "ready", id: result.id } };
}

/** A photo that has been made ready to upload. */
export interface Prepared {
  key: number;
  blob: Blob;
}

interface AttachIo {
  dispatch: (action: AttachedAction) => void;
  /** Whether the photo is still the one that is attached. */
  isCurrent: () => boolean;
  upload: (photo: Prepared) => Promise<unknown>;
}

export interface Chosen {
  file: File;
  note?: string;
}

/** The one file to take of those that were dropped: a picture, if there is one. */
export function dropped(files: File[]): Chosen | undefined {
  const file = files.find((f) => f.type.startsWith("image/")) ?? files[0];
  if (!file) return undefined;
  return files.length === 1 ? { file } : { file, note: "One photo was taken. You can attach one at a time." };
}

/** Shrink and upload a photo the customer chose. A file that cannot be read is never sent. */
export async function attachPhoto(
  { file, note }: Chosen,
  key: number,
  io: AttachIo,
  shrinkImpl: (file: File) => Promise<Blob> = shrink,
) {
  io.dispatch({ type: "chosen", key, note });
  let blob: Blob;
  try {
    blob = await shrinkImpl(file);
  } catch {
    io.dispatch({ type: "uploaded", key, result: { status: "rejected", message: UNREADABLE } });
    return;
  }
  if (io.isCurrent()) await io.upload({ key, blob });
}

/** The photo could not be uploaded again, so the turn was not sent. */
export class PhotoNotSent extends Error {}

/**
 * The events of a turn that names the photo, if there is one. When the API no longer has it (15
 * minutes, or a restart), the photo is uploaded once more and the turn is sent again. When it is
 * lost then too, `lostAgain` is told and the error is shown.
 */
export async function* withPhoto(
  photo: AttachedPhoto | undefined,
  send: (attachments: string[]) => AsyncGenerator<ServerEvent>,
  uploadAgain: (photo: Prepared) => Promise<UploadResult>,
  lostAgain: (key: number) => void,
): AsyncGenerator<ServerEvent> {
  if (!photo?.id || !photo.blob) {
    yield* send([]);
    return;
  }
  let lost = false;
  for await (const event of send([photo.id])) {
    lost = isLost(event);
    if (lost) break;
    yield event;
  }
  if (!lost) return;
  const result = await uploadAgain({ key: photo.key, blob: photo.blob });
  if (result.status !== "ready") throw new PhotoNotSent(result.message);
  for await (const event of send([result.id])) {
    if (isLost(event)) lostAgain(photo.key);
    yield event;
  }
}

function isLost(event: ServerEvent): boolean {
  return event.type === "error" && event.data.code === "unknown_attachment";
}
