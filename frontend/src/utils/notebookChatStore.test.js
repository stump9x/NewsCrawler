import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import {
  trimChatToLastTurns,
  loadNotebookChatHistory,
  saveNotebookChatHistory,
  pruneExpiredNotebookChatHistory,
  notebookChatScopeKey,
  NOTEBOOK_CHAT_MAX_TURNS,
  NOTEBOOK_CHAT_HISTORY_TTL_MS,
} from "./notebookChatStore";

describe("trimChatToLastTurns", () => {
  it("keeps last N human+ai exchanges", () => {
    const msgs = [];
    for (let i = 0; i < 12; i += 1) {
      msgs.push({ type: "human", content: `q${i}` });
      msgs.push({ type: "ai", content: `a${i}` });
    }
    const trimmed = trimChatToLastTurns(msgs, 10);
    expect(trimmed).toHaveLength(20);
    expect(trimmed[0].content).toBe("q2");
    expect(trimmed[trimmed.length - 1].content).toBe("a11");
  });

  it("defaults to NOTEBOOK_CHAT_MAX_TURNS", () => {
    expect(NOTEBOOK_CHAT_MAX_TURNS).toBe(30);
  });

  it("builds stable scope keys", () => {
    expect(notebookChatScopeKey([])).toBe("__all__");
    expect(notebookChatScopeKey(["b", "a"])).toBe("a|b");
  });
});

describe("notebook chat history localStorage", () => {
  const id = "notebook:test-hist";

  beforeEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  afterEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it("saves and restores up to 30 turns", () => {
    const msgs = [];
    for (let i = 0; i < 15; i += 1) {
      msgs.push({ type: "human", content: `h${i}` });
      msgs.push({ type: "ai", content: `r${i}` });
    }
    saveNotebookChatHistory(id, msgs);
    const loaded = loadNotebookChatHistory(id);
    expect(loaded).toHaveLength(30);
    expect(loaded[0].content).toBe("h0");
    expect(loaded[29].content).toBe("r14");
  });

  it("returns empty when missing", () => {
    expect(loadNotebookChatHistory("notebook:none")).toEqual([]);
  });

  it("partitions history by source scope key", () => {
    saveNotebookChatHistory(
      id,
      [
        { type: "human", content: "all q" },
        { type: "ai", content: "all a" },
      ],
      "__all__"
    );
    saveNotebookChatHistory(
      id,
      [
        { type: "human", content: "scoped q" },
        { type: "ai", content: "scoped a" },
      ],
      "source:a"
    );
    expect(loadNotebookChatHistory(id, "__all__")[0].content).toBe("all q");
    expect(loadNotebookChatHistory(id, "source:a")[0].content).toBe("scoped q");
  });

  it("timestamps saves and expires exactly after 24h TTL", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-24T10:00:00Z"));
    saveNotebookChatHistory(id, [
      { type: "human", content: "old q" },
      { type: "ai", content: "old a" },
    ]);
    const key = `nc_nb_chat_hist_v1:${id}::__all__`;
    const raw = JSON.parse(localStorage.getItem(key));
    expect(raw.savedAt).toBe(Date.now());
    expect(raw.updatedAt).toBe(raw.savedAt);
    expect(raw.expiresAt).toBe(raw.savedAt + 24 * 60 * 60 * 1000);
    expect(loadNotebookChatHistory(id)).toHaveLength(2);

    vi.setSystemTime(
      new Date(Date.now() + NOTEBOOK_CHAT_HISTORY_TTL_MS + 1000)
    );
    expect(loadNotebookChatHistory(id)).toEqual([]);
    expect(localStorage.getItem(key)).toBeNull();
  });

  it("preserves readable source metadata across reload", () => {
    saveNotebookChatHistory(id, [
      { type: "human", content: "nguồn nào" },
      {
        type: "ai",
        content: "Nội dung trả lời.",
        source_url: "https://example.com/source",
        source_title: "Nguồn đầy đủ",
      },
    ]);
    const loaded = loadNotebookChatHistory(id);
    expect(loaded[1]).toMatchObject({
      source_url: "https://example.com/source",
      source_title: "Nguồn đầy đủ",
    });
  });

  it("pruneExpiredNotebookChatHistory removes stale scoped keys", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-24T10:00:00Z"));
    saveNotebookChatHistory(
      id,
      [{ type: "human", content: "a" }, { type: "ai", content: "b" }],
      "scope:x"
    );
    saveNotebookChatHistory(
      "notebook:other",
      [{ type: "human", content: "c" }, { type: "ai", content: "d" }],
      "__all__"
    );
    vi.setSystemTime(
      new Date(Date.now() + NOTEBOOK_CHAT_HISTORY_TTL_MS + 5000)
    );
    const removed = pruneExpiredNotebookChatHistory();
    expect(removed).toBeGreaterThanOrEqual(2);
    expect(loadNotebookChatHistory(id, "scope:x")).toEqual([]);
    expect(loadNotebookChatHistory("notebook:other")).toEqual([]);
  });
});
