export type UploadResult =
  | { status: "ready"; id: string }
  | { status: "rejected"; message: string } // a photo, but not good enough
  | { status: "error"; message: string };

const UPLOAD_MS = 60_000;

/** Runa tells the driver these, so they are in plain words. */
const REFUSED: Record<number, string> = {
  401: "Your sign-in is no longer valid. Sign out and in again.",
  403: "Your account is not allowed to send photos.",
  408: "The photo took too long to send. Check the connection and try again.",
  413: "The photo is too large.",
  415: "The photo cannot be read.",
  503: "Voltrieve is busy with other photos. Wait a moment and try again.",
};
const FAILED = "The photo could not be sent. Try again.";

/** POST /attachments. Never throws for network or HTTP errors. */
export async function uploadPhoto(
  input: { apiUrl: string; token: string; blob: Blob; label: string },
  fetchImpl: typeof fetch = fetch,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", input.blob, "photo.jpg");
  form.append("label", input.label);
  let response: Response;
  try {
    response = await fetchImpl(`${input.apiUrl.replace(/\/+$/, "")}/attachments`, {
      method: "POST",
      headers: { Authorization: `Bearer ${input.token}`, Accept: "application/json" },
      body: form,
      signal: AbortSignal.timeout(UPLOAD_MS),
    });
  } catch {
    return { status: "error", message: "The photo could not be sent. Check the connection and try again." };
  }
  if (!response.ok) return { status: "error", message: REFUSED[response.status] ?? FAILED };
  const answer = await response.json().catch(() => null);
  if (answer?.status === "ready" && typeof answer.id === "string") return { status: "ready", id: answer.id };
  if (answer?.status === "rejected" && typeof answer.message === "string") {
    return { status: "rejected", message: answer.message };
  }
  return { status: "error", message: FAILED };
}

interface Photo {
  block: number;
  blob: Blob;
  upload?: Promise<UploadResult>;
  id?: string;
}

export type Upload = (photo: { block: number; blob: Blob }) => Promise<UploadResult>;
export type Renewed = { ids: string[] } | { message: string };

/** As many as the API keeps for one user. */
const MAX_PHOTOS = 10;

/** The photos of the signs the car has passed. They stay here, because the API may lose them. */
export class SignPhotos {
  private photos: Photo[] = []; // the last one is the newest
  private url?: string;

  add(block: number, blob: Blob): void {
    this.photos = [...this.photos, { block, blob }].slice(-MAX_PHOTOS);
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = URL.createObjectURL(blob);
  }

  last(): { block: number; blob: Blob; url: string } | undefined {
    const photo = this.photos.at(-1);
    return photo && { block: photo.block, blob: photo.blob, url: this.url! };
  }

  /** The id of the last photo. It is uploaded the first time, and the id is kept. */
  async idForLast(upload: Upload): Promise<(UploadResult & { block: number }) | { status: "none"; message: string }> {
    const photo = this.photos.at(-1);
    if (!photo) return { status: "none", message: "No parking sign has been passed yet." };
    return { ...(await this.uploaded(photo, upload)), block: photo.block };
  }

  /** New ids for photos that the API has lost. The same photos, also when a newer one has come. */
  async renew(ids: string[], upload: Upload): Promise<Renewed> {
    const fresh: string[] = [];
    for (const id of ids) {
      const photo = this.photos.find((p) => p.id === id);
      if (!photo) return { message: "The photo is gone. Ask again." };
      photo.upload = undefined;
      const result = await this.uploaded(photo, upload);
      if (result.status !== "ready") return { message: result.message };
      fresh.push(result.id);
    }
    return { ids: fresh };
  }

  private async uploaded(photo: Photo, upload: Upload): Promise<UploadResult> {
    const running = (photo.upload ??= upload(photo));
    try {
      const result = await running;
      if (result.status === "ready") photo.id = result.id;
      if (result.status === "error") photo.upload = undefined; // the next question tries again
      return result;
    } catch (e) {
      photo.upload = undefined;
      throw e;
    }
  }
}
