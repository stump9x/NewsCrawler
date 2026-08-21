/** Prefer Vietnamese Wire title; fall back to original while translation is pending. */
export function displayWireTitle(row) {
  if (!row || typeof row !== "object") return "—";
  const vi = typeof row.title_vi === "string" ? row.title_vi.trim() : "";
  if (vi) return vi;
  const original = typeof row.title === "string" ? row.title.trim() : "";
  return original || "Đang dịch…";
}
