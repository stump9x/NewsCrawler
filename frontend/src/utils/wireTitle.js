const CJK_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]/;
const DANGLING_VI_RE = /\b(?:dự|của|về|với|cho|từ|tại|trong|để|nhằm|theo|đang|sẽ)$/i;
const ENGLISH_HEADLINE_RE = /\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){2,}\b/;

function looksIncomplete(vi, original) {
  if (!vi || !original) return false;
  if (CJK_RE.test(vi) || ENGLISH_HEADLINE_RE.test(vi)) return true;
  const sourceWords = original.match(/[A-Za-zÀ-ỹĐđ]{2,}/g) || [];
  const translatedWords = vi.match(/[A-Za-zÀ-ỹĐđ]{2,}/g) || [];
  if (sourceWords.length >= 6 && translatedWords.length < Math.max(5, Math.floor(sourceWords.length * 0.45))) return true;
  return translatedWords.length < 7 && DANGLING_VI_RE.test(vi.trim());
}

/** Prefer a complete Vietnamese Wire title; hide known truncated/mixed drafts. */
export function displayWireTitle(row) {
  if (!row || typeof row !== "object") return "—";
  const vi = typeof row.title_vi === "string" ? row.title_vi.trim() : "";
  const original = typeof row.title === "string" ? row.title.trim() : "";
  if (vi && !looksIncomplete(vi, original)) return vi;
  return original || "Đang dịch…";
}
