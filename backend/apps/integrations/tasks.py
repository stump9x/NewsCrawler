from celery import shared_task
import logging

from apps.integrations.ai.briefings import (
    create_ai_briefing,
    create_keyword_summary,
    create_weekly_trending_digest,
)
from apps.integrations.misp.sync import (
    export_indicators_to_misp,
    import_attributes_from_misp,
)

logger = logging.getLogger(__name__)


@shared_task(
    name="integrations.generate_daily_briefing",
    soft_time_limit=900,
    time_limit=960,
)
def generate_daily_briefing(window_hours: int = 24, briefing_id: int | None = None) -> dict:
    from celery.exceptions import SoftTimeLimitExceeded
    from apps.integrations.ai.briefings import (
        create_ai_briefing,
        fill_ai_briefing,
        finalize_briefing_on_timeout,
    )
    from apps.integrations.models import AIBriefing

    briefing = None
    try:
        if briefing_id:
            briefing = AIBriefing.objects.get(pk=briefing_id)
            briefing = fill_ai_briefing(briefing)
        else:
            briefing = create_ai_briefing(window_hours=window_hours)
    except AIBriefing.DoesNotExist:
        return {"id": briefing_id, "status": "purged", "provider": ""}
    except SoftTimeLimitExceeded:
        if briefing_id and briefing is None:
            briefing = AIBriefing.objects.filter(pk=briefing_id).first()
        if briefing is not None and briefing.status != AIBriefing.Status.READY:
            briefing = finalize_briefing_on_timeout(
                briefing, reason="celery soft time limit"
            )
        elif briefing is None:
            raise
    return {
        "id": briefing.id,
        "status": briefing.status,
        "provider": briefing.provider,
    }


@shared_task(
    name="integrations.generate_weekly_digest",
    soft_time_limit=900,
    time_limit=960,
)
def generate_weekly_digest(briefing_id: int | None = None) -> dict:
    from celery.exceptions import SoftTimeLimitExceeded
    from apps.integrations.ai.briefings import (
        create_weekly_trending_digest,
        fill_weekly_trending_digest,
        finalize_briefing_on_timeout,
    )
    from apps.integrations.models import AIBriefing

    briefing = None
    try:
        if briefing_id:
            briefing = AIBriefing.objects.get(pk=briefing_id)
            briefing = fill_weekly_trending_digest(briefing)
        else:
            briefing = create_weekly_trending_digest()
    except AIBriefing.DoesNotExist:
        return {"id": briefing_id, "status": "purged", "provider": ""}
    except SoftTimeLimitExceeded:
        if briefing_id:
            briefing = AIBriefing.objects.filter(pk=briefing_id).first()
        if briefing is not None and briefing.status != AIBriefing.Status.READY:
            briefing = finalize_briefing_on_timeout(
                briefing, reason="celery soft time limit"
            )
        elif briefing is None:
            raise
    return {
        "id": briefing.id,
        "status": briefing.status,
        "provider": briefing.provider,
    }


@shared_task(name="integrations.misp_export")
def misp_export_task(limit: int = 50) -> dict:
    log = export_indicators_to_misp(limit=limit)
    return {
        "id": log.id,
        "status": log.status,
        "message": log.message,
        "records_processed": log.records_processed,
    }


@shared_task(name="integrations.misp_import")
def misp_import_task(limit: int = 50) -> dict:
    log = import_attributes_from_misp(limit=limit)
    return {
        "id": log.id,
        "status": log.status,
        "message": log.message,
        "records_processed": log.records_processed,
    }


@shared_task(
    name="integrations.keyword_summary",
    soft_time_limit=900,
    time_limit=960,
)
def keyword_summary_task(
    keyword: str, window_hours: int = 168, briefing_id: int | None = None
) -> dict:
    from celery.exceptions import SoftTimeLimitExceeded
    from apps.integrations.ai.briefings import (
        create_keyword_summary,
        fill_keyword_summary,
        finalize_briefing_on_timeout,
    )
    from apps.integrations.models import AIBriefing

    briefing = None
    try:
        if briefing_id:
            briefing = AIBriefing.objects.get(pk=briefing_id)
            briefing = fill_keyword_summary(briefing)
        else:
            briefing = create_keyword_summary(
                keyword=keyword, window_hours=window_hours
            )
    except AIBriefing.DoesNotExist:
        return {"id": briefing_id, "status": "purged", "provider": ""}
    except SoftTimeLimitExceeded:
        if briefing_id:
            briefing = AIBriefing.objects.filter(pk=briefing_id).first()
        if briefing is not None and briefing.status != AIBriefing.Status.READY:
            briefing = finalize_briefing_on_timeout(
                briefing, reason="celery soft time limit"
            )
        elif briefing is None:
            raise
    return {
        "id": briefing.id,
        "status": briefing.status,
        "provider": briefing.provider,
    }


@shared_task(bind=True, name="integrations.scan_searx_leaks", max_retries=2)
def scan_searx_leaks(self, limit_per_keyword: int = 15) -> dict:
    """Watcher-style periodic SearxNG keyword sweep → Data Leaks."""
    from apps.integrations.searx.leak_scan import scan_leak_keywords_via_searx

    try:
        return scan_leak_keywords_via_searx(limit_per_keyword=limit_per_keyword)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=60) from exc


@shared_task(bind=True, name="integrations.scan_document_pdfs", max_retries=0)
def scan_document_pdfs(
    self,
    limit_per_keyword: int | None = None,
    force: bool = False,
    generation: int | None = None,
    attempt: int = 1,
) -> dict:
    """
    Google/Searx PDF dork sweep → ScannedDocument + path alerts.

    No Celery retries: on hard failure we supersede this run and kick a fresh
    scan once. Stale generations (old queue messages) are no-ops.
    """
    from django.conf import settings

    from apps.core.task_lock import single_flight
    from apps.integrations.searx.document_scan import scan_documents_via_searx
    from apps.integrations.searx.document_scan_runner import (
        LOCK_NAME,
        document_scan_lock_ttl_sec,
        enqueue_document_scan,
        is_current_generation,
    )

    if not bool(getattr(settings, "DOCUMENT_SCAN_ENABLED", False)):
        return {"skipped": True, "reason": "document_scan_disabled"}

    if not is_current_generation(generation):
        return {"skipped": True, "reason": "superseded", "generation": generation}

    with single_flight(LOCK_NAME, ttl_sec=document_scan_lock_ttl_sec()) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        if not is_current_generation(generation):
            return {"skipped": True, "reason": "superseded", "generation": generation}

        # Leave "queued" as soon as this worker holds the lock (before Searx work).
        from apps.integrations.searx.document_scan_status import (
            mark_scan_finished,
            mark_scan_running,
        )

        mark_scan_running(total_keywords=0, generation=int(generation or 0))
        try:
            result = scan_documents_via_searx(
                limit_per_keyword=limit_per_keyword,
                force=force,
                publish_progress=True,
            )
            return {**result, "generation": generation, "attempt": attempt}
        except Exception as exc:  # noqa: BLE001
            logger.exception("document_scan failed attempt=%s: %s", attempt, exc)
            restarted = False
            if int(attempt or 1) < 2:
                enqueue_document_scan(
                    limit_per_keyword=limit_per_keyword,
                    force=force,
                    attempt=int(attempt or 1) + 1,
                    countdown=15,
                    terminate_running=False,
                )
                restarted = True
            else:
                mark_scan_finished(error=str(exc))
            return {
                "ok": False,
                "error": str(exc),
                "restarted": restarted,
                "attempt": attempt,
                "generation": generation,
            }


@shared_task(name="integrations.kick_document_scan")
def kick_document_scan(
    limit_per_keyword: int | None = None,
    force: bool = False,
) -> dict:
    """Beat/API entry: replace any prior scan and start exactly one fresh run."""
    from django.conf import settings

    from apps.integrations.searx.document_scan_runner import enqueue_document_scan

    if not bool(getattr(settings, "DOCUMENT_SCAN_ENABLED", False)):
        return {"skipped": True, "reason": "document_scan_disabled"}

    return enqueue_document_scan(
        limit_per_keyword=limit_per_keyword,
        force=force,
        terminate_running=True,
    )


@shared_task(bind=True, name="integrations.enrich_searx_leak", max_retries=2)
def enrich_searx_leak(self, leak_id: int) -> dict:
    """Fetch page body for a Searx/Exa DataLeak and attach secret evidence."""
    from apps.intel.models import DataLeak
    from apps.integrations.web_reader.enrich import enrich_leak_from_url

    try:
        leak = DataLeak.objects.filter(pk=leak_id).first()
        if not leak:
            return {"skipped": True, "reason": "missing"}
        keyword = str((leak.metadata or {}).get("keyword") or "")
        return enrich_leak_from_url(leak, keyword=keyword)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=45) from exc


@shared_task(bind=True, name="integrations.discover_unstable_intel_sites", max_retries=2)
def discover_unstable_intel_sites(self, limit_per_domain: int = 5) -> dict:
    """Searx fallback for curated sites without stable RSS → filtered Wire items."""
    from apps.integrations.searx.site_discovery import discover_unstable_site_items
    from apps.workers.services import ingest_rss_items

    try:
        items, discovery = discover_unstable_site_items(
            limit_per_domain=limit_per_domain
        )
        stats = ingest_rss_items(items, source_label="searx-site")
        return {
            **stats,
            **discovery,
            "fetched": len(items),
        }
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=120) from exc


@shared_task(bind=True, name="integrations.discover_exa_wire", max_retries=2)
def discover_exa_wire(
    self, limit: int | None = None, limit_per_domain: int | None = None
) -> dict:
    """Exa semantic CTI + curated domains → The Wire (Threat ingest)."""
    from django.conf import settings

    from apps.core.task_lock import single_flight
    from apps.integrations.web_reader.exa_wire import discover_exa_wire_items
    from apps.workers.services import ingest_rss_items

    if limit is None:
        limit = int(getattr(settings, "EXA_WIRE_LIMIT", 8) or 8)
    if limit_per_domain is None:
        limit_per_domain = int(getattr(settings, "EXA_WIRE_LIMIT_PER_DOMAIN", 2) or 2)

    with single_flight("integrations.discover_exa_wire", ttl_sec=900) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            items, discovery = discover_exa_wire_items(
                limit=limit, limit_per_domain=limit_per_domain
            )
            if discovery.get("skipped"):
                return {**discovery, "fetched": 0, "created": 0}
            stats = ingest_rss_items(items, source_label="exa-wire")
            return {
                **stats,
                **discovery,
                "fetched": len(items),
            }
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=120) from exc


@shared_task(bind=True, name="integrations.discover_x_wire", max_retries=2)
def discover_x_wire(
    self, limit_per_account: int | None = None
) -> dict:
    """Curated X CTI accounts → The Wire (Threat ingest)."""
    from django.conf import settings

    from apps.core.task_lock import single_flight
    from apps.integrations.web_reader.x_wire import discover_x_wire_items
    from apps.workers.services import ingest_rss_items

    if limit_per_account is None:
        limit_per_account = int(
            getattr(settings, "X_WIRE_LIMIT_PER_ACCOUNT", 8) or 8
        )

    with single_flight("integrations.discover_x_wire", ttl_sec=900) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            items, discovery = discover_x_wire_items(
                limit_per_account=limit_per_account
            )
            if discovery.get("skipped"):
                return {**discovery, "fetched": 0, "created": 0}
            stats = ingest_rss_items(items, source_label="x-wire")
            return {
                **stats,
                **discovery,
                "fetched": len(items),
            }
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=120) from exc


@shared_task(bind=True, name="integrations.translate_threat_titles", max_retries=1)
def translate_threat_titles_task(
    self, threat_ids: list[int] | None = None, limit: int = 12
) -> dict:
    """Translate Wire titles via Groq (paced) + stuck failover (sequential)."""
    from apps.core.task_lock import single_flight
    from apps.integrations.ai.translate import groq_ready_now, translate_threats

    # One translator at a time avoids rate-limit bursts and duplicate writes.
    with single_flight("integrations.translate_threat_titles", ttl_sec=300) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            # When Groq is cooling, allow a slightly larger stuck→Google drain
            # without raising concurrency (still sequential + paced).
            effective = min(int(limit or 12), 16)
            if not threat_ids and not groq_ready_now():
                effective = max(effective, 14)
            return translate_threats(threat_ids, limit=effective)
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=60) from exc


@shared_task(bind=True, name="integrations.translate_document_titles", max_retries=1)
def translate_document_titles_task(
    self, document_ids: list[int] | None = None, limit: int = 20
) -> dict:
    """Translate ScannedDocument titles (Google / Ollama, Wire doctrine)."""
    from django.conf import settings

    from apps.core.task_lock import single_flight
    from apps.integrations.ai.translate import translate_scanned_documents

    if not bool(getattr(settings, "DOCUMENT_SCAN_ENABLED", False)):
        return {"skipped": True, "reason": "document_scan_disabled"}

    with single_flight("integrations.translate_document_titles", ttl_sec=480) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            return translate_scanned_documents(document_ids, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=30) from exc


@shared_task(bind=True, name="integrations.translate_last30days_titles", max_retries=1)
def translate_last30days_titles_task(
    self, finding_ids: list[int] | None = None, limit: int = 25
) -> dict:
    """Translate last30days finding titles — Groq first, Google/Ollama fallback."""
    from apps.core.task_lock import single_flight
    from apps.integrations.last30days.translate import translate_last30days_findings

    with single_flight(
        "integrations.translate_last30days_titles", ttl_sec=480
    ) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            return translate_last30days_findings(
                finding_ids, limit=min(int(limit or 25), 40)
            )
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=45) from exc


@shared_task(bind=True, name="integrations.enrich_last30days_findings", max_retries=1)
def enrich_last30days_findings_task(
    self, finding_ids: list[int] | None = None, limit: int = 20
) -> dict:
    """Fetch thin last30days snippets via Wigolo before/alongside translation."""
    from apps.core.task_lock import single_flight
    from apps.integrations.last30days.enrich import enrich_findings

    with single_flight(
        "integrations.enrich_last30days_findings", ttl_sec=600
    ) as acquired:
        if not acquired:
            return {"skipped": True, "reason": "already_running"}
        try:
            return enrich_findings(finding_ids or [], limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise self.retry(exc=exc, countdown=30) from exc


@shared_task(
    bind=True,
    name="integrations.last30days_deep_brief",
    soft_time_limit=300,
    time_limit=360,
    max_retries=1,
)
def last30days_deep_brief_task(self, research_id: int) -> dict:
    """Re-synthesize findings-grounded brief (optional refresh / deep pass)."""
    from apps.integrations.last30days.brief import synthesize_research_brief
    from apps.integrations.models import Last30DaysResearch

    research = Last30DaysResearch.objects.filter(pk=research_id).first()
    if research is None:
        return {"ok": False, "error": "not_found"}
    if research.item_count <= 0:
        return {"ok": False, "id": research.id, "error": "no_findings"}

    out = synthesize_research_brief(research, force=True)
    if out.get("ok"):
        return {
            "ok": True,
            "id": research.id,
            "chars": out.get("chars") or 0,
            "provider": out.get("provider") or "",
            "finding_count": out.get("finding_count") or 0,
        }
    return {
        "ok": False,
        "id": research.id,
        "error": out.get("error") or "failed",
    }


@shared_task(name="integrations.run_github_scan")
def run_github_scan_task(scan_id: int) -> dict:
    from django.db import transaction

    from apps.integrations.github.scanner import run_github_scan
    from apps.integrations.models import GitHubScan

    with transaction.atomic():
        scan = GitHubScan.objects.select_for_update().get(pk=scan_id)
        if scan.status != GitHubScan.Status.QUEUED:
            return {
                "id": scan.id,
                "status": scan.status,
                "skipped": True,
            }
        scan.status = GitHubScan.Status.RUNNING
        scan.save(update_fields=["status", "updated_at"])
    run_github_scan(scan)
    return {
        "id": scan.id,
        "status": scan.status,
        "repositories": scan.repository_count,
        "files": scan.file_count,
        "alerts": scan.alert_count,
    }


@shared_task(
    name="integrations.run_last30days_research",
    soft_time_limit=840,
    time_limit=900,
)
def run_last30days_research_task(research_id: int) -> dict:
    from django.db import transaction

    from apps.integrations.last30days.service import run_last30days_research
    from apps.integrations.models import Last30DaysResearch

    with transaction.atomic():
        research = Last30DaysResearch.objects.select_for_update().get(pk=research_id)
        if research.status != Last30DaysResearch.Status.QUEUED:
            return {
                "id": research.id,
                "status": research.status,
                "skipped": True,
            }
        research.status = Last30DaysResearch.Status.RUNNING
        research.save(update_fields=["status", "updated_at"])
    run_last30days_research(research)
    research.refresh_from_db()
    return {
        "id": research.id,
        "status": research.status,
        "items": research.item_count,
        "duration_ms": research.duration_ms,
    }
