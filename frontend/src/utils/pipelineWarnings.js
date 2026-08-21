/**
 * Fetch/empty-body pipeline warnings are noisy for users — answer from
 * readable sources only. Keep them out of chat/briefing UI; server still logs.
 */

const SUPPRESS_RE =
  /không đọc được|empty response|đã thử fallback|fetch[_ ]?fail|hard-failed/i;

export function isSuppressedPipelineWarning(text) {
  return SUPPRESS_RE.test(String(text || ""));
}

/** Filter user-facing pipeline warnings (empty-fetch etc.). */
export function filterUserFacingPipelineWarnings(warnings) {
  if (!Array.isArray(warnings)) return [];
  return warnings
    .map((w) => String(w || "").trim())
    .filter((w) => w && !isSuppressedPipelineWarning(w));
}
