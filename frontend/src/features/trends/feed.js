import { api } from "../../api/client";

export const PROVIDERS = [
  { id: "newsnow", label: "NewsNow", url: "https://newsnow.busiyi.world" },
  { id: "sopilot", label: "SoPilot · X", url: "https://sopilot.net/zh/hot-tweets" },
  { id: "rebang", label: "REBANG", url: "https://rebang.open2hub.com" },
];
export const CJK = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

export function readSaved(key, fallback) {
  try { return JSON.parse(localStorage.getItem(`nc:trend:v3:${key}`)) ?? fallback; }
  catch { return fallback; }
}
export function save(key, value) {
  try { localStorage.setItem(`nc:trend:v3:${key}`, JSON.stringify(value)); }
  catch { /* A full/private browser cache must not prevent rendering. */ }
}
export async function request(path, { method = "get", body, signal } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  const timer = setTimeout(abort, method === "post" ? 40000 : 18000);
  try {
    const url = `/api/v1/trend/${path}`;
    const opts = { signal: controller.signal, retries: 0 };
    return method === "post" ? await api.post(url, body, opts) : await api.get(url, opts);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

// Keep every character, including the tail of long X posts. The server accepts
// bounded batches, so long posts are translated as complete paragraph chunks.
export function textChunks(text, limit = 1800) {
  const chunks = [];
  let remaining = String(text || "");
  while (remaining.length > limit) {
    let split = remaining.lastIndexOf("\n", limit);
    if (split < limit / 2) split = remaining.lastIndexOf(" ", limit);
    if (split < limit / 2) split = limit;
    else split += 1;
    chunks.push(remaining.slice(0, split));
    remaining = remaining.slice(split);
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}
export function translation(text, dictionary, supplied) {
  if (supplied && !CJK.test(supplied)) return supplied;
  if (dictionary[text]) return dictionary[text];
  const chunks = textChunks(text);
  return chunks.length && chunks.every((chunk) => dictionary[chunk])
    ? chunks.map((chunk) => dictionary[chunk]).join("\n") : null;
}
export function pendingTexts(boards, dictionary) {
  const texts = [];
  const add = (text, supplied) => {
    if (text && !translation(text, dictionary, supplied)) texts.push(...textChunks(text));
  };
  for (const board of boards) {
    if (CJK.test(board.name)) add(board.name, board.name_vi);
    add(board.subtitle, board.subtitle_vi);
  }
  // Interleave boards so the first screen gets Vietnamese titles everywhere.
  const count = Math.max(0, ...boards.map((board) => board.items.length));
  for (let rank = 0; rank < count; rank += 1) {
    for (const board of boards) {
      const item = board.items[rank];
      if (item) add(item.title, item.title_vi);
    }
  }
  return [...new Set(texts)].filter((text) => !dictionary[text]);
}
export function count(value) {
  const number = Number(value);
  return value == null || !Number.isFinite(number) ? "—" : new Intl.NumberFormat("vi-VN", { notation: "compact", maximumFractionDigits: 1 }).format(number);
}
export function dateLabel(value) {
  if (!value) return "";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}
