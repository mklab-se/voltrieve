/** What went wrong with a request, from the answer of the API: the code it gave, and a clear text. */
export interface Problem {
  code: string;
  message: string;
}

const BY_CODE: Record<string, string> = {
  unknown_attachment: "The API no longer has the photo. Ask again.",
  busy: "The API is busy with other photos. Wait a moment and try again.",
};

const BY_STATUS: Record<number, string> = {
  401: "The API rejected the access token (401). Try signing in again.",
  403: "The API refused the request (403). Check that your account has the Chat.User role, that the token carries the access_as_user scope, and that the conversation is yours.",
  408: "The upload took too long. Check the connection and try again.",
  409: "Runa is still answering the previous message. Wait a moment and try again.",
  413: "This is too large for the API. A photo may be 8 MB and a message 64 KB.",
  415: "The API does not know this kind of file. Send a JPEG, PNG or WebP photo.",
  503: "The API is not available at the moment (503). Try again later.",
};

export async function readProblem(response: Response): Promise<Problem> {
  let code: unknown;
  try {
    code = (await response.json())?.detail?.code;
  } catch {
    // Not every error has a JSON body.
  }
  if (typeof code === "string" && BY_CODE[code]) return { code, message: BY_CODE[code] };
  return {
    code: typeof code === "string" ? code : `http_${response.status}`,
    message: BY_STATUS[response.status] ?? `The API answered with HTTP ${response.status}.`,
  };
}
