#!/usr/bin/env sh
# Post-build / post-test cleanup — free disk + reclaim RAM.
# Run after every `docker compose build` / frontend npm build / pytest.
# On this shared VPS also run BreachSentinel's copy when that project built.
# Usage: sh scripts/post-build-cleanup.sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Routine cleanup is intentionally non-destructive. Enable the heavier actions
# only for an explicit maintenance run: PRUNE_DOCKER=1 PRUNE_HOST_CACHES=1
# DROP_PAGE_CACHE=1 sh scripts/post-build-cleanup.sh
PRUNE_DOCKER="${PRUNE_DOCKER:-0}"
PRUNE_HOST_CACHES="${PRUNE_HOST_CACHES:-0}"
DROP_PAGE_CACHE="${DROP_PAGE_CACHE:-0}"
echo "[post-build-cleanup] project=$(basename "$ROOT") prune_docker=$PRUNE_DOCKER drop_page_cache=$DROP_PAGE_CACHE"

# --- Frontend / TypeScript caches (never commit these) ---
rm -rf frontend/node_modules/.vite \
       frontend/node_modules/.cache \
       frontend/.vite \
       frontend/coverage \
       frontend/.turbo \
       frontend/dist 2>/dev/null || true
find frontend -name '*.tsbuildinfo' -type f -delete 2>/dev/null || true
find frontend -path '*/.vitest/*' -delete 2>/dev/null || true

# --- Python test / lint caches ---
find . \( -path './.git' -o -path './data' -o -path './vendor' -o -path '*/node_modules/*' \) -prune -o \
  \( -type d -name '__pycache__' -o -type d -name '.pytest_cache' -o -type d -name '.mypy_cache' -o -type d -name '.ruff_cache' \) \
  -print0 2>/dev/null | xargs -0 rm -rf 2>/dev/null || true
rm -rf .coverage htmlcov .tox .nox 2>/dev/null || true

# --- Host package manager caches (opt-in; preserve caches for fast rebuilds) ---
if [ "$PRUNE_HOST_CACHES" = "1" ]; then
  npm cache clean --force >/dev/null 2>&1 || true
  yarn cache clean >/dev/null 2>&1 || true
  pip cache purge >/dev/null 2>&1 || true
  apt-get clean >/dev/null 2>&1 || true
else
  echo "[post-build-cleanup] keep host package caches (set PRUNE_HOST_CACHES=1 to purge)"
fi

# --- Docker leftovers (opt-in; never prune during a routine build) ---
if [ "$PRUNE_DOCKER" = "1" ]; then
  docker container prune -f >/dev/null 2>&1 || true
  docker network prune -f >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -f >/dev/null 2>&1 || true
else
  echo "[post-build-cleanup] keep Docker layers/containers (set PRUNE_DOCKER=1 for maintenance)"
fi

# --- Unload idle Ollama models (NewsCrawler) — keeps images on disk ---
if docker compose ps --status running 2>/dev/null | grep -q ollama; then
  for m in qwen2.5:3b qwen2.5:1.5b nomic-embed-text; do
    docker compose exec -T ollama ollama stop "$m" >/dev/null 2>&1 || true
  done
fi

# --- Drop Linux page cache only by explicit request ---
# Linux already reclaims buff/cache under pressure. Dropping it after every
# build makes the next build slower and usually provides no lasting benefit.
if [ "$DROP_PAGE_CACHE" = "1" ] && [ "$(id -u)" = "0" ] && [ -w /proc/sys/vm/drop_caches ]; then
  sync
  echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
  echo "[post-build-cleanup] explicitly dropped Linux page cache"
else
  echo "[post-build-cleanup] kept Linux page cache (reclaimed automatically under pressure)"
fi

echo "[post-build-cleanup] memory after:"
free -h | head -2
echo "[post-build-cleanup] done"
