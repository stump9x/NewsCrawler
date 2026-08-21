/**
 * Wire date helpers: display + sort must share the same effective instant
 * so "just now" / future published_at cannot jump ahead of real news.
 */

function parseIso(iso) {
  if (!iso) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  return when;
}

function formatCalendarDate(when) {
  return [
    String(when.getDate()).padStart(2, "0"),
    String(when.getMonth() + 1).padStart(2, "0"),
    when.getFullYear(),
  ].join("/");
}

export function relativeTimeLabel(iso, now = Date.now()) {
  const when = parseIso(iso);
  if (!when) return null;

  const elapsedMs = Math.max(0, Number(now) - when.getTime());
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes >= 24 * 60) {
    const days = Math.floor(minutes / (24 * 60));
    return `${days} ngày trước`;
  }
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${hours} giờ trước`;
  }
  if (minutes >= 1) {
    return `${minutes} phút trước`;
  }
  return "vừa xong";
}

/**
 * Instant used for Wire/Overview labels and client-side ordering.
 * - Prefer published_at when it is a sane past timestamp
 * - Future / "just now" published while created_at is older → created_at
 * - Missing published → created_at
 */
export function wireDisplayInstant(row, now = Date.now()) {
  const published = parseIso(row?.published_at);
  const created = parseIso(row?.created_at);
  const nowMs = Number(now);

  if (!published && !created) return null;
  if (!published) return created;

  const pubMs = published.getTime();
  if (pubMs > nowMs + 60_000) {
    if (created && created.getTime() <= nowMs + 60_000) return created;
    return new Date(nowMs);
  }

  const pubAge = nowMs - pubMs;
  if (pubAge < 60_000 && created) {
    const createdAge = nowMs - created.getTime();
    // published looks brand-new but the row has been on the board longer
    if (createdAge >= 60_000) return created;
  }

  return published;
}

export function wireDisplayInstantMs(row, now = Date.now()) {
  const when = wireDisplayInstant(row, now);
  return when ? when.getTime() : 0;
}

/** Overview / equal-priority Wire: newest effective time first. */
export function compareByWireDisplayTime(a, b, now = Date.now()) {
  const tb = wireDisplayInstantMs(b, now);
  const ta = wireDisplayInstantMs(a, now);
  if (tb !== ta) return tb - ta;
  return (b.id || 0) - (a.id || 0);
}

/** The Wire: newest effective time first (priority is display-only, not sort). */
export function compareWireRows(a, b, now = Date.now()) {
  return compareByWireDisplayTime(a, b, now);
}

export function formatDateWithRelative(iso, now = Date.now()) {
  const when = parseIso(iso);
  if (!when) return "—";

  const relative = relativeTimeLabel(iso, now);
  return `${formatCalendarDate(when)} · ${relative}`;
}

/**
 * The Wire / Overview date cell — always derived from wireDisplayInstant.
 */
export function formatWireDateWithRelative(row, now = Date.now()) {
  const when = wireDisplayInstant(row, now);
  if (!when) return "—";
  return formatDateWithRelative(when.toISOString(), now);
}
