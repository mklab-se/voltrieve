import { readProblem } from "./problems";

export type UploadResult =
  | { status: "ready"; id: string }
  | { status: "rejected"; message: string } // a photo, but not good enough
  | { status: "error"; message: string };

const UPLOAD_MS = 60_000;

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
    return { status: "error", message: `Could not reach the API at ${input.apiUrl}.` };
  }
  if (!response.ok) return { status: "error", message: (await readProblem(response)).message };
  const answer = await response.json().catch(() => null);
  if (answer?.status === "ready" && typeof answer.id === "string") return { status: "ready", id: answer.id };
  if (answer?.status === "rejected" && typeof answer.message === "string") {
    return { status: "rejected", message: answer.message };
  }
  return { status: "error", message: "The car did not understand the API's answer to the photo." };
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
