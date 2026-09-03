#!/bin/sh
# Compose starts workers only after this process serves a healthy API.
set -eu

python manage.py migrate --noinput
python manage.py ensure_superuser
python manage.py prepare_wire_topics
python manage.py seed_rss_sources --deactivate-missing --force-activate
python manage.py seed_document_scan_keywords

exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-1}" --timeout 120
