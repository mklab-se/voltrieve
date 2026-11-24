import { useCallback, useEffect, useReducer, useRef } from "react";
import { attachedReducer, attachPhoto, uploadPhoto, type Attached, type Chosen, type Prepared, type UploadResult } from "./attachments";
import type { Auth } from "./auth";

const LABEL = "Photo from the customer"; // what the model sees, so not the file name

export interface PhotoControls {
  attached: Attached;
  choose: (chosen: Chosen) => void;
  retry: () => void;
  remove: () => void;
}

interface UsePhoto extends PhotoControls {
  upload: (photo: Prepared) => Promise<UploadResult>;
  /** The API has lost the photo, also after a new upload. */
  lost: (key: number) => void;
  /** The photo is with a message that is on its way. */
  sent: (key: number) => void;
  /** The message with the photo is over, answered or not. */
  over: (key: number, answered: boolean) => void;
  reset: () => void;
}

/** The photo attached to the chat, uploaded as soon as it is chosen. */
export function usePhoto(apiUrl: string, auth: Auth): UsePhoto {
  const [attached, dispatch] = useReducer(attachedReducer, {});
  const lastKey = useRef(0);
  const current = useRef(0);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  const upload = useCallback(
    async ({ key, blob }: Prepared): Promise<UploadResult> => {
      dispatch({ type: "uploading", key, blob });
      const ctrl = new AbortController();
      abort.current = ctrl;
      let result: UploadResult;
      try {
        const input = { apiUrl, token: await auth.getToken(), blob, label: LABEL };
        result = await uploadPhoto(input, (url, init) => fetch(url, { ...init, signal: ctrl.signal }));
      } catch {
        result = { status: "error", message: "You could not be signed in, so the photo was not uploaded. Try again." };
      }
      dispatch({ type: "uploaded", key, result });
      return result;
    },
    [apiUrl, auth],
  );

  const lost = useCallback((key: number) => dispatch({ type: "lost", key }), []);
  const sent = useCallback((key: number) => dispatch({ type: "sent", key }), []);
  const over = useCallback(
    (key: number, answered: boolean) => dispatch({ type: answered ? "answered" : "unanswered", key }),
    [],
  );

  function drop() {
    abort.current?.abort(); // one user may have few uploads at a time
    current.current = 0;
  }

  return {
    attached,
    upload,
    lost,
    sent,
    over,
    choose(chosen) {
      drop();
      const key = (lastKey.current += 1);
      current.current = key;
      void attachPhoto(chosen, key, { dispatch, isCurrent: () => current.current === key, upload });
    },
    retry() {
      const { photo } = attached;
      if (photo?.blob) void upload({ key: photo.key, blob: photo.blob });
    },
    remove() {
      drop();
      dispatch({ type: "remove" });
    },
    reset() {
      drop();
      dispatch({ type: "reset" });
    },
  };
}
