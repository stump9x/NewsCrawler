// Keep requests alive as boards arrive; cancel on timeout or leaving the page.
// Two batches may overlap, but starts are spaced to respect provider budgets.
export function createTranslationQueue({ translate, onResult, onStatus = () => {}, concurrency = 2, spacing = 5000, maxItems = 48, maxChars = 3600, requestTimeout = 45000, now = Date.now, schedule = setTimeout, unschedule = clearTimeout }) {
  let wanted = [];
  let stopped = false;
  let timer;
  let nextStart = 0;
  let cooldown = 0;
  let reason = "";
  let preferRetry = false;
  const done = new Set();
  const pending = new Map();
  const running = new Map();
  const attempts = new Map();

  function publish() {
    onStatus({ active: running.size, pending: wanted.filter((text) => !done.has(text)).length, reason });
  }
  function wake(delay = 0) {
    if (stopped) return;
    unschedule(timer);
    timer = schedule(pump, Math.max(0, delay));
  }
  function defer(missing, wait, batchSize, shrink = false, inProgress = false) {
    for (const text of missing) {
      const previous = attempts.get(text);
      const count = inProgress ? previous?.count || 0 : (previous?.count || 0) + 1;
      attempts.set(text, {
        count,
        // Persistent content errors gradually become single-item requests.
        limit: shrink ? Math.max(1, Math.floor(batchSize / 2)) : previous?.limit || maxItems,
        retryAt: now() + Math.max(wait, Math.min(60000, wait * 2 ** Math.min(Math.max(0, count - 1), 3))),
      });
    }
  }
  function dispatch(batch, controller) {
    // Free the slot even if a transport ignores cancellation or never settles.
    let deadline;
    let abort;
    const result = new Promise((resolve, reject) => {
      abort = () => reject(new Error("Translation request interrupted"));
      controller.signal.addEventListener("abort", abort, { once: true });
      deadline = schedule(() => controller.abort(), requestTimeout);
      Promise.resolve().then(() => {
        if (controller.signal.aborted) throw new Error("Translation request interrupted");
        return translate(batch, controller.signal);
      }).then(resolve, reject);
    });
    return result.finally(() => {
      unschedule(deadline);
      controller.signal.removeEventListener("abort", abort);
    });
  }
  function pump() {
    if (stopped) return;
    const time = now();
    const available = wanted.filter((text) => !done.has(text) && !pending.has(text));
    if (!available.length || running.size >= concurrency) { publish(); return; }
    const readyAt = Math.max(nextStart, cooldown, Math.min(...available.map((text) => attempts.get(text)?.retryAt || 0)));
    if (readyAt > time) { publish(); wake(readyAt - time); return; }
    const ready = available.filter((text) => (attempts.get(text)?.retryAt || 0) <= time);
    const retries = ready.filter((text) => attempts.has(text));
    const fresh = ready.filter((text) => !attempts.has(text));
    // Alternate so new headlines and retries cannot starve each other.
    const candidates = retries.length && (preferRetry || !fresh.length) ? retries : fresh;
    preferRetry = candidates === fresh;
    let limit = attempts.get(candidates[0])?.limit || maxItems;
    const batch = [];
    let size = 0;
    for (const text of candidates) {
      if (batch.length >= limit) break;
      const itemLimit = attempts.get(text)?.limit || maxItems;
      if (batch.length && (size + text.length > maxChars || batch.length >= itemLimit)) continue;
      // feed.textChunks keeps individual entries below the API's hard limit.
      // Let a lone entry exceed our soft budget rather than stall the queue.
      limit = Math.min(limit, itemLimit);
      batch.push(text); size += text.length;
    }
    if (!batch.length) { publish(); return; }
    const controller = new AbortController();
    running.set(controller, batch);
    for (const text of batch) pending.set(text, controller);
    nextStart = time + spacing;
    publish();
    dispatch(batch, controller).then((response) => {
      if (stopped) return;
      const accepted = {};
      for (const row of Array.isArray(response?.items) ? response.items : []) {
        if (row && batch.includes(row.text) && row.status === "ok" && typeof row.vi === "string" && row.vi.trim() && !/[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(row.vi)) {
          accepted[row.text] = row.vi;
          done.add(row.text);
          attempts.delete(row.text);
        }
      }
      if (Object.keys(accepted).length) onResult(accepted);
      const missing = batch.filter((text) => !done.has(text));
      reason = missing.length ? response?.reason || "invalid_translation" : "";
      const wait = Math.max(5, Math.min(300, Number(response?.retry_after) || 5)) * 1000;
      defer(missing, wait, batch.length, ["invalid_translation", "payload_too_large"].includes(reason), reason === "in_progress");
      // A shared quota failure applies to unseen batches too. Avoid a burst
      // of guaranteed failures, without parking individual items for minutes.
      if (!Object.keys(accepted).length && ["rate_limited", "unavailable"].includes(reason)) cooldown = Math.max(cooldown, now() + wait);
    }).catch((error) => {
      if (stopped) return;
      reason = error?.status === 429 ? "rate_limited" : "unavailable";
      const wait = error?.status === 429 ? 30000 : 10000;
      if (![400, 413].includes(error?.status)) cooldown = Math.max(cooldown, now() + wait);
      defer(batch.filter((text) => !done.has(text)), wait, batch.length, error?.status !== 429);
    }).finally(() => {
      running.delete(controller);
      for (const text of batch) pending.delete(text);
      if (!stopped) { publish(); wake(); }
    });
    wake(spacing);
  }
  return {
    update(texts) {
      wanted = [...new Set(texts)];
      const retained = new Set([...wanted, ...pending.keys()]);
      for (const text of done) if (!retained.has(text)) done.delete(text);
      for (const text of attempts.keys()) if (!retained.has(text)) attempts.delete(text);
      wake();
    },
    resume() { wake(); }, // Reconnection preserves the provider's cooldown.
    retry() { for (const attempt of attempts.values()) attempt.retryAt = 0; reason = ""; wake(); },
    stop() { stopped = true; unschedule(timer); running.forEach((_, controller) => controller.abort()); },
  };
}
