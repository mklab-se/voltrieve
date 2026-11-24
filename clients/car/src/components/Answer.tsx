// Copied from clients/web; keep the two alike.
import { memo, useMemo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { linkCitations, sourceFromHref } from "../sources";
import type { SourceItem } from "../types";

interface Props {
  text: string;
  sources?: SourceItem[];
  onOpenSource?: (source: SourceItem) => void;
}

/** While one answer streams, the others stay as they are. */
export const Answer = memo(function Answer({ text, sources, onOpenSource }: Props) {
  const components = useMemo(() => parts(sources, onOpenSource), [sources, onOpenSource]);
  return (
    <div className="answer">
      {/* A picture would be loaded from an address that the model chose. */}
      <Markdown remarkPlugins={PLUGINS} skipHtml disallowedElements={BLOCKED} components={components}>
        {linkCitations(text, sources)}
      </Markdown>
    </div>
  );
});

const PLUGINS = [remarkGfm];
const BLOCKED = ["img"];

function parts(sources: Props["sources"], onOpenSource: Props["onOpenSource"]): Components {
  // The text comes from a model, so raw HTML is dropped and links open away from the app. A
  // cited document becomes a button: its link is signed by the API when it is clicked.
  return {
    a: ({ children, href }) => {
      const id = sourceFromHref(href);
      const source = id ? sources?.find((s) => s.id === id) : undefined;
      if (id !== undefined) {
        return source && onOpenSource ? (
          <button type="button" className="citation" onClick={() => onOpenSource(source)} title={`Open ${source.title}`}>
            {children}
          </button>
        ) : (
          <>{children}</>
        );
      }
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
    table: ({ children }) => (
      <div className="table-wrap">
        <table>{children}</table>
      </div>
    ),
  };
}
