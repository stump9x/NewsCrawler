import { Fragment } from "react";
import { splitInlineMarkdown } from "../utils/inlineMarkdown";

/**
 * Render inline markdown emphasis as real <strong>/<em> (no HTML injection).
 * Parent should keep whiteSpace: pre-wrap so newlines stay visible.
 */
export function InlineMarkdown({ text }) {
  const parts = splitInlineMarkdown(text);
  return (
    <>
      {parts.map((p, i) => {
        if (p.type === "strong") {
          return <strong key={`md-${i}`}>{p.value}</strong>;
        }
        if (p.type === "em") {
          return <em key={`md-${i}`}>{p.value}</em>;
        }
        return <Fragment key={`md-${i}`}>{p.value}</Fragment>;
      })}
    </>
  );
}
