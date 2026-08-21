/**
 * Lightweight safe inline-markdown split for Notebook chat + briefing UI.
 * Renders **bold** / __bold__ / *italic* / _italic_ without showing raw markers.
 * Does not interpret HTML — consumers render as React text nodes only.
 */

const INLINE_EMPH_RE =
  /\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\n]+)\*|_([^_\n]+)_/g;

/**
 * @param {string} text
 * @returns {{ type: 'text' | 'strong' | 'em', value: string }[]}
 */
export function splitInlineMarkdown(text) {
  const input = String(text ?? "");
  if (!input) return [{ type: "text", value: "" }];

  const parts = [];
  let last = 0;
  INLINE_EMPH_RE.lastIndex = 0;
  let m = INLINE_EMPH_RE.exec(input);
  while (m) {
    if (m.index > last) {
      parts.push({ type: "text", value: input.slice(last, m.index) });
    }
    if (m[1] != null) parts.push({ type: "strong", value: m[1] });
    else if (m[2] != null) parts.push({ type: "strong", value: m[2] });
    else if (m[3] != null) parts.push({ type: "em", value: m[3] });
    else if (m[4] != null) parts.push({ type: "em", value: m[4] });
    last = m.index + m[0].length;
    m = INLINE_EMPH_RE.exec(input);
  }
  if (last < input.length) {
    parts.push({ type: "text", value: input.slice(last) });
  }
  return parts.length ? parts : [{ type: "text", value: input }];
}

/** True when text still has paired emphasis markers worth rendering. */
export function hasInlineMarkdown(text) {
  const s = String(text || "");
  return (
    /\*\*[^*]+\*\*/.test(s) ||
    /__[^_]+__/.test(s) ||
    /(^|[^*])\*([^*\n]+)\*(?!\*)/.test(s) ||
    /(^|[^_])_([^_\n]+)_(?!_)/.test(s)
  );
}
