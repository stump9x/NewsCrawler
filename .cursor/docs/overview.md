# NewsCrawler — Project Overview

Defense / military / nuclear **news intelligence** platform (“The Wire”). Shared ~8GB VPS with BreachSentinel.

## Stack

| Layer | Tech |
|-------|------|
| API | Django + DRF (`backend/`) |
| Workers | Celery + Redis |
| UI | React + Vite + MUI (`frontend/`) |
| DB | PostgreSQL 16 (`nc-postgres`) |
| Local LLM | Ollama `qwen2.5:3b` (`nc-ollama`, shared with BreachSentinel) |
| Search | SearXNG |
| Notebook | SurrealDB + notebook-ai / gateway / Wigolo |

Compose: `docker-compose.yml`. Containers prefixed `nc-*`.

## Layout

```
NewsCrawler/
├── backend/
│   ├── apps/core/           # auth, health, users
│   ├── apps/intel/          # Threat, FeedSource, WatchRule, documents…
│   ├── apps/workers/        # RSS ingest, Celery tasks, feeds/
│   ├── apps/integrations/   # AI briefings, notebook chat, SearX
│   └── config/              # settings, urls, celery
├── frontend/src/
│   ├── pages/               # Dashboard, Wire, Sources, Briefings, NotebookAI, Last30Days
│   ├── api/ components/ layout/ auth/
├── scripts/                 # post-build-cleanup.sh, ram-guard.sh
└── vendor/last30days/
```

## Core domain (`apps/intel`)

- **Threat** — Wire articles (`raw_payload.feed` = FeedSource `name`; no FK)
- **FeedSource** — RSS catalog (seeded from `apps/workers/feeds/rss_sources.json`)
- **WatchRule / AlertNotification** — alerts
- **ScannedDocument / DocumentScanKeyword** — document scan (often disabled on VPS)
- **Indicator / ThreatActor / DataLeak / CompromisedCredential** — shared CTI models (lighter use than BreachSentinel)

API prefix: `/api/v1/` (DRF routers in `apps/intel/urls.py`).

## Ingest path

1. `seed_rss_sources` on backend boot (`--deactivate-missing --force-activate`)
2. Celery pulls active `FeedSource` rows via `load_active_rss_feeds()`
3. Items → `Threat` with `raw_payload.feed` / `feed_url`, military relevance filter
4. Removing a source permanently: delete Threats + FeedSource **and** remove from `rss_sources.json`

## UI routes

`/`, `/feeds` (WirePage), `/sources`, `/intelligence` (BriefingsPage), `/notebook-ai`, `/last30days`, `/login`

## Ops constraints (VPS)

- After builds/tests: run `scripts/post-build-cleanup.sh` (+ BreachSentinel sibling)
- Celery/Gunicorn concurrency **1**; keep `WIGOLO_EAGER_WARMUP=0`, document scan off unless raising RAM
- Never wipe named volumes, `.env`, or running production images

## Sibling project

`/root/BreachSentinel` — leak/OSINT/CTI focus; shares Ollama + Docker host RAM.
