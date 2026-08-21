/**
 * Notebook AI chat persistence + in-flight jobs that survive panel unmount
 * (tab switch within Notebook AI, or navigating away and back).
 *
 * History: last N user↔assistant exchanges per notebookId (localStorage).
 * Auto-expires exactly 24h after the latest save (FE-only, pruned on reopen).
 * Pending: module-level Map so fetch continues when React unmounts the panel.
 * Abort only via explicit cancel or hang timeout — never on route leave.
 */

const HISTORY_PREFIX = "nc_nb_chat_hist_v1:";
const PENDING_PREFIX = "nc_nb_chat_pending_v1:";
const EVENT = "nc-nb-chat";

/** Keep a full working-day conversation: 30 exchanges / 60 messages. */
export const NOTEBOOK_CHAT_MAX_TURNS = 30;

/** Hang timeout mirrored in the interactive UI. */
export const NOTEBOOK_CHAT_HANG_MS = 15 * 1000;

/** Drop chat transcripts 24 hours after their latest save. */
export const NOTEBOOK_CHAT_HISTORY_TTL_MS = 24 * 60 * 60 * 1000;

/** Fallback check when there is no stored history. */
const PRUNE_IDLE_CHECK_MS = 60 * 60 * 1000;

const jobs = new Map();
let pruneTimer = null;

/** Stable scope key for partitioning chat history (empty = whole notebook). */
export function notebookChatScopeKey(selectedSourceIds = []) {
  const ids = (Array.isArray(selectedSourceIds) ? selectedSourceIds : [])
    .map((id) => String(id || "").trim())
    .filter(Boolean)
    .sort();
  return ids.length ? ids.join("|") : "__all__";
}

function historyKey(notebookId, scopeKey = "__all__") {
  const nb = String(notebookId || "").trim();
  const scope = String(scopeKey || "__all__").slice(0, 240) || "__all__";
  return `${HISTORY_PREFIX}${nb}::${scope}`;
}

function pendingKey(notebookId) {
  return `${PENDING_PREFIX}${String(notebookId || "").trim()}`;
}

function emit(notebookId, detail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(EVENT, { detail: { notebookId, ...detail } })
  );
}

function historySavedAt(data) {
  return Number(data?.savedAt) || Number(data?.updatedAt) || 0;
}

function historyExpiresAt(data) {
  const explicit = Number(data?.expiresAt) || 0;
  if (explicit > 0) return explicit;
  const saved = historySavedAt(data);
  return saved > 0 ? saved + NOTEBOOK_CHAT_HISTORY_TTL_MS : 0;
}

function isHistoryExpired(data) {
  const expiresAt = historyExpiresAt(data);
  return !expiresAt || Date.now() >= expiresAt;
}

function nextHistoryExpiry() {
  if (typeof localStorage === "undefined") return 0;
  let earliest = 0;
  for (let i = 0; i < localStorage.length; i += 1) {
    const key = localStorage.key(i);
    if (!key || !key.startsWith(HISTORY_PREFIX)) continue;
    try {
      const data = JSON.parse(localStorage.getItem(key) || "null");
      const expiresAt = historyExpiresAt(data);
      if (expiresAt > 0 && (!earliest || expiresAt < earliest)) earliest = expiresAt;
    } catch {
      return Date.now();
    }
  }
  return earliest;
}

/**
 * Scan localStorage and delete expired notebook chat history entries.
 * @returns {number} number of keys removed
 */
export function pruneExpiredNotebookChatHistory() {
  if (typeof localStorage === "undefined") return 0;
  const keys = [];
  for (let i = 0; i < localStorage.length; i += 1) {
    const k = localStorage.key(i);
    if (k && k.startsWith(HISTORY_PREFIX)) keys.push(k);
  }
  let removed = 0;
  for (const k of keys) {
    try {
      const raw = localStorage.getItem(k);
      if (!raw) {
        localStorage.removeItem(k);
        removed += 1;
        continue;
      }
      const data = JSON.parse(raw);
      if (isHistoryExpired(data)) {
        localStorage.removeItem(k);
        removed += 1;
      }
    } catch {
      localStorage.removeItem(k);
      removed += 1;
    }
  }
  return removed;
}

/** Schedule pruning at the nearest exact expiresAt while the app is open. */
export function ensureNotebookChatHistoryPrune() {
  if (typeof window === "undefined") return;
  pruneExpiredNotebookChatHistory();
  if (pruneTimer != null) window.clearTimeout(pruneTimer);
  const next = nextHistoryExpiry();
  const delay = next
    ? Math.max(0, Math.min(next - Date.now(), PRUNE_IDLE_CHECK_MS))
    : PRUNE_IDLE_CHECK_MS;
  pruneTimer = window.setTimeout(() => {
    pruneTimer = null;
    pruneExpiredNotebookChatHistory();
    ensureNotebookChatHistoryPrune();
  }, delay);
}

/**
 * Keep the last `maxTurns` human→ai pairs (orphan trailing human kept).
 * @param {Array<{type?: string, role?: string, content?: string}>} messages
 * @param {number} [maxTurns]
 */
export function trimChatToLastTurns(
  messages,
  maxTurns = NOTEBOOK_CHAT_MAX_TURNS
) {
  const list = Array.isArray(messages) ? messages.filter(Boolean) : [];
  if (!list.length || maxTurns <= 0) return [];
  const turns = [];
  let i = 0;
  while (i < list.length) {
    const m = list[i];
    const role = m.type || m.role;
    if (role === "human" || role === "user") {
      const next = list[i + 1];
      const nextRole = next?.type || next?.role;
      if (next && (nextRole === "ai" || nextRole === "assistant")) {
        turns.push([
          { type: "human", content: String(m.content || "") },
          {
            type: "ai",
            content: String(next.content || ""),
            ...(next.source_url ? { source_url: String(next.source_url) } : {}),
            ...(next.source_title ? { source_title: String(next.source_title) } : {}),
            ...(Array.isArray(next.quotes) ? { quotes: next.quotes.slice(0, 3) } : {}),
            ...(next._pending ? { _pending: true } : {}),
            ...(next._status ? { _status: next._status } : {}),
          },
        ]);
        i += 2;
        continue;
      }
      turns.push([{ type: "human", content: String(m.content || "") }]);
      i += 1;
      continue;
    }
    // Leading AI (rare) — keep as half-turn
    turns.push([
      {
        type: "ai",
        content: String(m.content || ""),
        ...(m.source_url ? { source_url: String(m.source_url) } : {}),
        ...(m.source_title ? { source_title: String(m.source_title) } : {}),
        ...(Array.isArray(m.quotes) ? { quotes: m.quotes.slice(0, 3) } : {}),
        ...(m._pending ? { _pending: true } : {}),
      },
    ]);
    i += 1;
  }
  const kept = turns.slice(-maxTurns);
  return kept.flat();
}

export function loadNotebookChatHistory(notebookId, scopeKey = "__all__") {
  ensureNotebookChatHistoryPrune();
  if (!notebookId || typeof localStorage === "undefined") return [];
  try {
    const key = historyKey(notebookId, scopeKey);
    const raw = localStorage.getItem(key);
    if (!raw) {
      // Migrate legacy notebook-only key into whole-notebook scope once.
      if (scopeKey === "__all__") {
        const legacy = localStorage.getItem(
          `${HISTORY_PREFIX}${String(notebookId).trim()}`
        );
        if (legacy) {
          try {
            const legacyData = JSON.parse(legacy);
            if (isHistoryExpired(legacyData)) {
              localStorage.removeItem(
                `${HISTORY_PREFIX}${String(notebookId).trim()}`
              );
              return [];
            }
          } catch {
            localStorage.removeItem(
              `${HISTORY_PREFIX}${String(notebookId).trim()}`
            );
            return [];
          }
          localStorage.setItem(historyKey(notebookId, "__all__"), legacy);
          localStorage.removeItem(`${HISTORY_PREFIX}${String(notebookId).trim()}`);
          return loadNotebookChatHistory(notebookId, "__all__");
        }
      }
      return [];
    }
    const data = JSON.parse(raw);
    if (isHistoryExpired(data)) {
      localStorage.removeItem(key);
      return [];
    }
    const msgs = Array.isArray(data?.messages) ? data.messages : [];
    return trimChatToLastTurns(
      msgs.map((m) => ({
        type: m.type === "human" || m.role === "user" ? "human" : "ai",
        content: String(m.content || ""),
        ...(m.source_url ? { source_url: String(m.source_url) } : {}),
        ...(m.source_title ? { source_title: String(m.source_title) } : {}),
        ...(Array.isArray(m.quotes) ? { quotes: m.quotes.slice(0, 3) } : {}),
      }))
    );
  } catch {
    return [];
  }
}

export function saveNotebookChatHistory(
  notebookId,
  messages,
  scopeKey = "__all__"
) {
  ensureNotebookChatHistoryPrune();
  if (!notebookId || typeof localStorage === "undefined") return;
  try {
    const trimmed = trimChatToLastTurns(messages).map((m) => ({
      type: m.type === "human" ? "human" : "ai",
      content: String(m.content || ""),
      ...(m.source_url ? { source_url: String(m.source_url) } : {}),
      ...(m.source_title ? { source_title: String(m.source_title) } : {}),
      ...(Array.isArray(m.quotes) ? { quotes: m.quotes.slice(0, 3) } : {}),
    }));
    const now = Date.now();
    localStorage.setItem(
      historyKey(notebookId, scopeKey),
      JSON.stringify({
        savedAt: now,
        updatedAt: now,
        expiresAt: now + NOTEBOOK_CHAT_HISTORY_TTL_MS,
        scopeKey: String(scopeKey || "__all__"),
        messages: trimmed,
      })
    );
    ensureNotebookChatHistoryPrune();
  } catch {
    /* quota */
  }
}

/** Lightweight pending marker for reload UX (in-memory job is authoritative). */
export function writeNotebookChatPending(notebookId, pending) {
  if (!notebookId || typeof localStorage === "undefined") return;
  try {
    if (!pending) {
      localStorage.removeItem(pendingKey(notebookId));
    } else {
      localStorage.setItem(
        pendingKey(notebookId),
        JSON.stringify({
          startedAt: pending.startedAt || Date.now(),
          question: String(pending.question || "").slice(0, 240),
          status: String(pending.status || ""),
        })
      );
    }
  } catch {
    /* ignore */
  }
  emit(notebookId, { kind: "pending" });
}

export function readNotebookChatPending(notebookId) {
  if (!notebookId || typeof localStorage === "undefined") return null;
  try {
    const raw = localStorage.getItem(pendingKey(notebookId));
    if (!raw) return null;
    const data = JSON.parse(raw);
    const started = Number(data?.startedAt) || 0;
    if (started && Date.now() - started > NOTEBOOK_CHAT_HANG_MS + 30_000) {
      localStorage.removeItem(pendingKey(notebookId));
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export function getNotebookChatJob(notebookId) {
  if (!notebookId) return null;
  return jobs.get(String(notebookId)) || null;
}

/**
 * Register / update an in-flight chat job (survives React unmount).
 * @returns {object} job
 */
export function upsertNotebookChatJob(notebookId, patch = {}) {
  const id = String(notebookId || "");
  if (!id) return null;
  const prev = jobs.get(id) || {
    notebookId: id,
    busy: false,
    messages: [],
    error: "",
    usedModelLabel: "",
    contextMeta: null,
    status: "",
    startedAt: 0,
    abortController: null,
    listeners: new Set(),
  };
  const next = { ...prev, ...patch, notebookId: id, listeners: prev.listeners };
  if (patch.scopeKey != null) next.scopeKey = String(patch.scopeKey);
  jobs.set(id, next);
  writeNotebookChatPending(
    id,
    next.busy
      ? {
          startedAt: next.startedAt || Date.now(),
          question: next.question || "",
          status: next.status || "",
        }
      : null
  );
  for (const fn of next.listeners) {
    try {
      fn(next);
    } catch {
      /* ignore */
    }
  }
  emit(id, { kind: "job", busy: next.busy });
  return next;
}

export function clearNotebookChatJob(notebookId, { keepHistory = true } = {}) {
  const id = String(notebookId || "");
  const job = jobs.get(id);
  if (job?.busy && keepHistory) {
    // Soft-clear UI fields but leave listeners; caller should mark busy false.
  }
  writeNotebookChatPending(id, null);
  if (job) {
    const next = {
      ...job,
      busy: false,
      status: "",
      abortController: null,
      question: "",
    };
    jobs.set(id, next);
    for (const fn of next.listeners) {
      try {
        fn(next);
      } catch {
        /* ignore */
      }
    }
  }
}

/**
 * Subscribe to in-memory job updates for a notebook.
 * @returns {() => void} unsubscribe
 */
export function subscribeNotebookChatJob(notebookId, onChange) {
  const id = String(notebookId || "");
  if (!id || typeof onChange !== "function") return () => {};
  let job = jobs.get(id);
  if (!job) {
    job = {
      notebookId: id,
      busy: false,
      messages: [],
      error: "",
      usedModelLabel: "",
      contextMeta: null,
      status: "",
      startedAt: 0,
      abortController: null,
      listeners: new Set(),
    };
    jobs.set(id, job);
  }
  job.listeners.add(onChange);
  onChange(job);
  return () => {
    const cur = jobs.get(id);
    if (cur) cur.listeners.delete(onChange);
  };
}

/** Abort only when user cancels or hang timer fires. */
export function abortNotebookChatJob(notebookId, reason = "cancel") {
  const job = getNotebookChatJob(notebookId);
  if (!job?.abortController) return false;
  try {
    job.abortController.abort(reason);
  } catch {
    try {
      job.abortController.abort();
    } catch {
      /* ignore */
    }
  }
  return true;
}
