/** What a screen reader says about the chat. Pure. */
import type { ChatState } from "./turns";

/** The text without the marks of Markdown. A table becomes one line for each of its rows. */
export function plainText(markdown: string): string {
  return markdown
    .split("\n")
    .filter((line) => line.trim() !== "" && !/^\s*\|?[\s:|-]*-{3,}[\s:|-]*$/.test(line))
    .map((line) =>
      line
        .replace(/^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)/, "")
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/`+([^`]*)`+/g, "$1")
        .replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, "$2")
        .replace(/(?<![\w*])\*(?=\S)([^*]+?)(?<=\S)\*(?![\w*])/g, "$1")
        .replace(/(?<![\w_])_(?=\S)([^_]+?)(?<=\S)_(?![\w_])/g, "$1")
        .replace(/^\s*\|\s*|\s*\|\s*$/g, "")
        .replace(/\s*\|\s*/g, ", ")
        .trim(),
    )
    .join("\n");
}

/** What Runa is doing, and then the whole answer, not each word of it. An error has its own place. */
export function spoken({ turns }: ChatState, doing?: string): string {
  const last = turns.at(-1);
  if (!last || last.status === "error") return "";
  if (last.status === "streaming") return doing ?? last.activity ?? "Runa is thinking";
  return last.answer && `Runa: ${plainText(last.answer)}`;
}
