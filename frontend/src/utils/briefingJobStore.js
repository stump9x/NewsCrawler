/** Persist in-flight AI briefing so navigation / reload does not drop tracking. */

const STORAGE_KEY = "nc_active_briefing_job";
const EVENT = "nc-briefing-job";
/** Drop stale banner after 5 minutes so UI is not stuck forever. */
const JOB_TTL_MS = 5 * 60 * 1000;

export function readActiveBriefingJob() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data?.id) return null;
    const started = Number(data.startedAt) || 0;
    if (started && Date.now() - started > JOB_TTL_MS) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

export function writeActiveBriefingJob(job) {
  try {
    if (!job?.id) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          id: job.id,
          kind: job.kind || "",
          title: job.title || "",
          startedAt: job.startedAt || Date.now(),
          progress_pct: job.progress_pct ?? 0,
          progress: job.progress || "",
        })
      );
    }
  } catch {
    /* ignore quota */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: readActiveBriefingJob() }));
}

export function clearActiveBriefingJob() {
  writeActiveBriefingJob(null);
}

export function patchActiveBriefingJob(patch) {
  const cur = readActiveBriefingJob();
  if (!cur?.id) return;
  writeActiveBriefingJob({ ...cur, ...patch });
}

/** Subscribe to localStorage + same-tab custom events. */
export function subscribeActiveBriefingJob(onChange) {
  const emit = () => onChange(readActiveBriefingJob());
  const onStorage = (e) => {
    if (e.key === STORAGE_KEY || e.key === null) emit();
  };
  const onCustom = () => emit();
  window.addEventListener("storage", onStorage);
  window.addEventListener(EVENT, onCustom);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(EVENT, onCustom);
  };
}
