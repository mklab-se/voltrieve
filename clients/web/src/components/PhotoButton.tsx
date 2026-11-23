import { useEffect, useRef, useState, type Ref } from "react";
import type { AttachedPhoto } from "../attachments";

/** Show a photo through an object URL. Returns what to call when it is no longer shown. */
export function showBlob(blob: Blob, show: (url: string) => void): () => void {
  const url = URL.createObjectURL(blob);
  show(url);
  return () => URL.revokeObjectURL(url);
}

export function PhotoThumb({ blob, alt }: { blob: Blob; alt: string }) {
  const [url, setUrl] = useState<string>();
  useEffect(() => showBlob(blob, setUrl), [blob]);
  if (!url) return <span className="photo-thumb" role="img" aria-label={alt} />;
  return <img className="photo-thumb" src={url} alt={alt} />;
}

interface ButtonProps {
  disabled: boolean;
  onFile: (file: File) => void;
  ref?: Ref<HTMLButtonElement>;
}

export function PhotoButton({ disabled, onFile, ref }: ButtonProps) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        ref={ref}
        type="button"
        className="ghost photo-button"
        aria-label="Attach a photo"
        title="Attach a photo, or drop one here"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h3l1.6-2.5h5.8L16.5 7h3A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" />
          <circle cx="12" cy="13" r="3.6" />
        </svg>
      </button>
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        hidden
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // so that the same file can be chosen again
          if (file) onFile(file);
        }}
      />
    </>
  );
}

export const NOT_STORED = "Photos are not stored in the conversation.";

const STATUS: Record<AttachedPhoto["status"], string> = {
  preparing: "Reading the photo…",
  uploading: "Uploading the photo…",
  ready: "Goes with your questions until you remove it.",
  failed: "Not uploaded.",
};

interface PreviewProps {
  photo: AttachedPhoto;
  disabled: boolean;
  onRetry: () => void;
  onRemove: () => void;
}

export function PhotoPreview({ photo, disabled, onRetry, onRemove }: PreviewProps) {
  return (
    <div className="photo-preview" data-status={photo.status}>
      {photo.blob ? <PhotoThumb blob={photo.blob} alt="The attached photo" /> : <span className="photo-thumb" />}
      <p className="photo-text" role="status">
        <span>{STATUS[photo.status]}</span>
        <span className="photo-note">
          {photo.note && `${photo.note} `}
          {NOT_STORED}
        </span>
      </p>
      {photo.status === "failed" && (
        <button type="button" className="ghost" disabled={disabled} onClick={onRetry}>
          Try again
        </button>
      )}
      <button type="button" className="ghost icon" aria-label="Remove the photo" title="Remove the photo" disabled={disabled} onClick={onRemove}>
        ✕
      </button>
    </div>
  );
}
