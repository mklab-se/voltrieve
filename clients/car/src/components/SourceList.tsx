// Copied from clients/web; keep the two alike.
import type { SourceItem } from "../types";

interface Props {
  sources: SourceItem[];
  onOpen: (source: SourceItem) => void;
}

function Chip({ source, onOpen }: { source: SourceItem; onOpen: (s: SourceItem) => void }) {
  if (!source.link) {
    return (
      <span className="source-chip locked" title="Internal document: it cannot be opened from here">
        {source.title}
      </span>
    );
  }
  return (
    <button type="button" className={`source-chip ${source.kind}`} onClick={() => onOpen(source)} title={source.id}>
      {source.title}
    </button>
  );
}

/** The documents an answer used, then the ones it suggests reading. */
export function SourceList({ sources, onOpen }: Props) {
  const used = sources.filter((s) => s.kind === "source");
  const more = sources.filter((s) => s.kind === "read_more");
  return (
    <div className="sources">
      {used.length > 0 && (
        <div className="source-row" aria-label="Sources">
          <span className="source-label">Sources</span>
          {used.map((s) => <Chip key={s.id} source={s} onOpen={onOpen} />)}
        </div>
      )}
      {more.length > 0 && (
        <div className="source-row" aria-label="Read more">
          <span className="source-label">Read more</span>
          {more.map((s) => <Chip key={s.id} source={s} onOpen={onOpen} />)}
        </div>
      )}
    </div>
  );
}
