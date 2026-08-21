#!/usr/bin/env sh
# RAM guard — when MemAvailable drops below threshold, run safe post-build
# cleanup for NewsCrawler + BreachSentinel (shared VPS).
#
# Threshold (MiB): RAM_GUARD_MIN_AVAILABLE_MB (default 2048 = 2 GiB)
# Cooldown:        RAM_GUARD_COOLDOWN_SEC (default 900 = 15 min)
# Critical:        RAM_GUARD_CRITICAL_MB (default 1024) — bypass cooldown
# Force:           FORCE_RAM_CLEANUP=1 — ignore threshold + cooldown
# Log:             RAM_GUARD_LOG (default /var/log/ram-guard.log, else ~/ram-guard.log)
#
# Cron (every 2 min): */2 * * * * /root/NewsCrawler/scripts/ram-guard.sh
# Exit 0 when RAM OK (no cleanup) or cleanup finished; non-zero only on hard failure.
set -eu

NEWSCRAWLER="${NEWSCRAWLER_ROOT:-/root/NewsCrawler}"
BREACHSENTINEL="${BREACHSENTINEL_ROOT:-/root/BreachSentinel}"
MIN_MB="${RAM_GUARD_MIN_AVAILABLE_MB:-2048}"
COOLDOWN_SEC="${RAM_GUARD_COOLDOWN_SEC:-900}"
CRITICAL_MB="${RAM_GUARD_CRITICAL_MB:-1024}"
# Drop page cache when MemAvailable is at or below MIN_MB (default 2 GiB).
# Keep this opt-in switch available for operators who want a stricter guard.
DROP_PAGE_CACHE_ON_LOW_RAM="${RAM_GUARD_DROP_PAGE_CACHE:-1}"
STATE_DIR="${RAM_GUARD_STATE_DIR:-/var/tmp/ram-guard}"
STATE_FILE="${STATE_DIR}/last-cleanup"
LOCK_FILE="${STATE_DIR}/lock"

# Prefer /var/log; fall back to home if not writable
if [ -n "${RAM_GUARD_LOG:-}" ]; then
  LOG="$RAM_GUARD_LOG"
elif [ -w /var/log ] 2>/dev/null || touch /var/log/ram-guard.log 2>/dev/null; then
  LOG="/var/log/ram-guard.log"
else
  LOG="${HOME}/ram-guard.log"
fi

mkdir -p "$STATE_DIR" 2>/dev/null || true

log() {
  # shellcheck disable=SC2154
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >>"$LOG" 2>/dev/null || \
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2
}

# Available RAM in MiB (prefer MemAvailable)
available_mb() {
  if [ -r /proc/meminfo ]; then
    awk '/^MemAvailable:/ { printf "%d", $2 / 1024; found=1; exit }
         END { if (!found) exit 1 }' /proc/meminfo 2>/dev/null && return 0
  fi
  # Fallback: free -m "available" column (row Mem:)
  free -m 2>/dev/null | awk '/^Mem:/ { print $7; exit }'
}

now_epoch() {
  date +%s
}

in_cooldown() {
  [ ! -f "$STATE_FILE" ] && return 1
  last="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
  now="$(now_epoch)"
  elapsed=$((now - last))
  [ "$elapsed" -lt "$COOLDOWN_SEC" ]
}

acquire_lock() {
  # mkdir-based lock (portable, no flock required)
  if mkdir "$LOCK_FILE" 2>/dev/null; then
    return 0
  fi
  # Stale lock older than 30 min → reclaim
  if [ -d "$LOCK_FILE" ]; then
    age=$(( $(now_epoch) - $(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0) ))
    if [ "$age" -gt 1800 ]; then
      rmdir "$LOCK_FILE" 2>/dev/null || true
      mkdir "$LOCK_FILE" 2>/dev/null && return 0
    fi
  fi
  return 1
}

release_lock() {
  rmdir "$LOCK_FILE" 2>/dev/null || true
}

BEFORE="$(available_mb)"
BEFORE="${BEFORE:-0}"

FORCE="${FORCE_RAM_CLEANUP:-0}"

if [ "$FORCE" != "1" ]; then
  if [ "$BEFORE" -gt "$MIN_MB" ]; then
    # Quiet success when RAM is fine (cron every 2 min)
    exit 0
  fi
fi

# Below threshold (or forced)
if [ "$FORCE" != "1" ] && [ "$BEFORE" -ge "$CRITICAL_MB" ] && in_cooldown; then
  log "skip cooldown: available=${BEFORE}MiB threshold=${MIN_MB}MiB (last cleanup < ${COOLDOWN_SEC}s ago)"
  exit 0
fi

if ! acquire_lock; then
  log "skip locked: available=${BEFORE}MiB (another ram-guard running)"
  exit 0
fi
trap release_lock EXIT

REASON="low-ram"
[ "$FORCE" = "1" ] && REASON="forced"
[ "$BEFORE" -lt "$CRITICAL_MB" ] && [ "$FORCE" != "1" ] && REASON="critical"

log "START reason=${REASON} available_before=${BEFORE}MiB threshold=${MIN_MB}MiB critical=${CRITICAL_MB}MiB"

# Normal operation never reaches this block. If MemAvailable is below the
# configured 2 GiB threshold, pass the explicit flag to NewsCrawler's cleanup
# script; above it, the script keeps the kernel page cache untouched.
if [ "$DROP_PAGE_CACHE_ON_LOW_RAM" = "1" ] && [ "$BEFORE" -le "$MIN_MB" ]; then
  export DROP_PAGE_CACHE=1
  log "page-cache drop enabled: available=${BEFORE}MiB <= ${MIN_MB}MiB"
else
  export DROP_PAGE_CACHE=0
  log "page-cache drop disabled: available=${BEFORE}MiB > ${MIN_MB}MiB"
fi

run_cleanup() {
  script="$1"
  if [ -x "$script" ] || [ -f "$script" ]; then
    sh "$script" >>"$LOG" 2>&1 || log "WARN cleanup failed: $script (exit $?)"
  else
    log "WARN missing cleanup script: $script"
  fi
}

run_cleanup "$NEWSCRAWLER/scripts/post-build-cleanup.sh"
run_cleanup "$BREACHSENTINEL/scripts/post-build-cleanup.sh"

AFTER="$(available_mb)"
AFTER="${AFTER:-0}"
now_epoch >"$STATE_FILE" 2>/dev/null || true

log "DONE available_before=${BEFORE}MiB available_after=${AFTER}MiB delta=$((AFTER - BEFORE))MiB"
exit 0
