// Copied to clients/car; keep the two alike.
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { linkCitations, sourceFromHref } from "../sources";
import type { SourceItem } from "../types";

interface Props {
  text: string;
  sources?: SourceItem[];
  onOpenSource?: (source: SourceItem) => void;
}

export function Answer({ text, sources, onOpenSource }: Props) {
  // The text comes from a model, so raw HTML is dropped and links open away from the app. A
  // cited document becomes a button: its link is signed by the API when it is clicked.
  const components: Components = {
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
  return (
    <div className="answer">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {linkCitations(text, sources)}
      </Markdown>
    </div>
  );
}
