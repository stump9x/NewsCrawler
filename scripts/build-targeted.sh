#!/usr/bin/env sh
# Targeted deployment helper: build only the service whose source changed.
# Usage: sh scripts/build-targeted.sh frontend|backend|all|restart
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
target="${1:-}"

case "$target" in
  frontend)
    docker compose build frontend
    docker compose up -d --no-deps frontend
    ;;
  backend)
    docker compose build backend
    # backend, celery and beat share the backend image; do not restart databases.
    docker compose up -d --no-deps backend celery celery-beat
    ;;
  all)
    docker compose build backend frontend
    docker compose up -d --no-deps backend celery celery-beat frontend
    ;;
  restart)
    docker compose restart backend celery celery-beat frontend
    ;;
  *)
    echo "Usage: $0 frontend|backend|all|restart" >&2
    exit 2
    ;;
esac
