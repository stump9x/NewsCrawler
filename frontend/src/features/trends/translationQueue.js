// Keep requests alive as boards arrive. Only leaving the page cancels work.
// Two batches may overlap, but starts are spaced to respect provider budgets.
export function createTranslationQueue({ translate, onResult, onStatus, concurrency = 2, spacing = 4500, maxItems = 48, maxChars = 3600, now = Date.now, schedule = setTimeout, unschedule = clearTimeout }) {
  let wanted = [];
  let stopped = false;
  let timer;
  let nextStart = 0;
  let cooldown = 0;
  let reason = "";
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
  function pump() {
    if (stopped) return;
    const time = now();
    const available = wanted.filter((text) => !done.has(text) && !pending.has(text));
    if (!available.length || running.size >= concurrency) { publish(); return; }
    const readyAt = Math.max(nextStart, cooldown, Math.min(...available.map((text) => attempts.get(text)?.retryAt || 0)));
    if (readyAt > time) { publish(); wake(readyAt - time); return; }
    const batch = [];
    let size = 0;
    for (const text of available) {
      if ((attempts.get(text)?.retryAt || 0) > time) continue;
      if (batch.length >= maxItems || size + text.length > maxChars) break;
      batch.push(text); size += text.length;
    }
    if (!batch.length) { publish(); return; }
    const controller = new AbortController();
    running.set(controller, batch);
    for (const text of batch) pending.set(text, controller);
    nextStart = time + spacing;
    publish();
    Promise.resolve().then(() => translate(batch, controller.signal)).then((response) => {
      if (stopped) return;
      const accepted = {};
      for (const row of response.items || []) {
        if (batch.includes(row.text) && row.status === "ok" && typeof row.vi === "string" && row.vi.trim() && !/[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(row.vi)) {
          accepted[row.text] = row.vi;
          done.add(row.text);
          attempts.delete(row.text);
        }
      }
      if (Object.keys(accepted).length) onResult(accepted);
      const missing = batch.filter((text) => !done.has(text));
      reason = missing.length ? response.reason || "unavailable" : "";
      const wait = Math.max(5, Math.min(300, Number(response.retry_after) || 5)) * 1000;
      for (const text of missing) {
        const count = (attempts.get(text)?.count || 0) + 1;
        attempts.set(text, { count, retryAt: now() + Math.min(300000, wait * 2 ** Math.min(count - 1, 4)) });
      }
      // A shared quota failure applies to unseen batches too. Avoid a burst
      // of guaranteed failures, without parking individual items for minutes.
      if (!Object.keys(accepted).length && ["rate_limited", "unavailable"].includes(reason)) cooldown = now() + wait;
    }).catch((error) => {
      if (stopped) return;
      reason = error.status === 429 ? "rate_limited" : "unavailable";
      cooldown = now() + (error.status === 429 ? 30000 : 10000);
      for (const text of batch) attempts.set(text, { count: 1, retryAt: cooldown });
    }).finally(() => {
      running.delete(controller);
      for (const text of batch) pending.delete(text);
      if (!stopped) { publish(); wake(); }
    });
    wake(spacing);
  }
  return {
    update(texts) { wanted = [...new Set(texts)]; wake(); },
    retry() { attempts.clear(); cooldown = 0; reason = ""; wake(); },
    stop() { stopped = true; unschedule(timer); running.forEach((_, controller) => controller.abort()); },
  };
}
