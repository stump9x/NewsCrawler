from __future__ import annotations

from django.utils import timezone

from apps.intel.models import Indicator
from apps.integrations.misp.client import (
    MISPError,
    create_event,
    misp_configured,
    search_attributes,
)
from apps.integrations.models import IntegrationSyncLog

IOC_TO_MISP = {
    "ipv4": "ip-dst",
    "ipv6": "ip-dst",
    "domain": "domain",
    "url": "url",
    "email": "email",
    "md5": "md5",
    "sha1": "sha1",
    "sha256": "sha256",
    "cve": "vulnerability",
    "filename": "filename",
}

MISP_TO_IOC = {v: k for k, v in IOC_TO_MISP.items()}
MISP_TO_IOC.update(
    {
        "ip-src": "ipv4",
        "ip-dst": "ipv4",
        "hostname": "domain",
        "domain|ip": "domain",
    }
)


def export_indicators_to_misp(limit: int = 50) -> IntegrationSyncLog:
    if not misp_configured():
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.EXPORT,
            status=IntegrationSyncLog.Status.SKIPPED,
            message="MISP not configured (set MISP_URL and MISP_API_KEY)",
        )

    qs = Indicator.objects.filter(is_active=True).order_by("-last_seen")[:limit]
    attributes = []
    for ind in qs:
        misp_type = IOC_TO_MISP.get(ind.ioc_type)
        if not misp_type:
            continue
        attributes.append(
            {
                "type": misp_type,
                "value": ind.value[:1024],
                "category": "Network activity"
                if ind.ioc_type in {"ipv4", "ipv6", "domain", "url"}
                else "Payload delivery",
                "to_ids": True,
                "comment": f"NewsCrawler export source={ind.source}",
            }
        )

    if not attributes:
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.EXPORT,
            status=IntegrationSyncLog.Status.SKIPPED,
            message="No exportable indicators",
            records_processed=0,
        )

    try:
        result = create_event(
            info=f"NewsCrawler export {timezone.now().date().isoformat()}",
            attributes=attributes,
        )
        event = (result.get("Event") or {}) if isinstance(result, dict) else {}
        event_id = event.get("id")
        # Stamp misp uuid when present on attributes (best-effort)
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.EXPORT,
            status=IntegrationSyncLog.Status.SUCCESS,
            message=f"Exported event_id={event_id}",
            records_processed=len(attributes),
            details={"event_id": event_id, "attribute_count": len(attributes)},
        )
    except MISPError as exc:
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.EXPORT,
            status=IntegrationSyncLog.Status.FAILED,
            message=str(exc),
            records_processed=0,
            details={"status_code": exc.status_code},
        )


def import_attributes_from_misp(limit: int = 50) -> IntegrationSyncLog:
    if not misp_configured():
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.IMPORT,
            status=IntegrationSyncLog.Status.SKIPPED,
            message="MISP not configured (set MISP_URL and MISP_API_KEY)",
        )

    try:
        attrs = search_attributes(limit=limit)
    except MISPError as exc:
        return IntegrationSyncLog.objects.create(
            target=IntegrationSyncLog.Target.MISP,
            direction=IntegrationSyncLog.Direction.IMPORT,
            status=IntegrationSyncLog.Status.FAILED,
            message=str(exc),
            details={"status_code": exc.status_code},
        )

    created = 0
    updated = 0
    for attr in attrs:
        if not isinstance(attr, dict):
            continue
        misp_type = attr.get("type") or ""
        value = (attr.get("value") or "").strip()
        ioc_type = MISP_TO_IOC.get(misp_type)
        if not ioc_type or not value:
            continue
        _, was_created = Indicator.objects.update_or_create(
            ioc_type=ioc_type,
            normalized_value=Indicator.normalize(ioc_type, value),
            defaults={
                "value": value[:2048],
                "source": "misp",
                "confidence": Indicator.Confidence.HIGH,
                "description": attr.get("comment") or "Imported from MISP",
                "misp_attribute_uuid": str(attr.get("uuid") or "")[:64],
                "last_seen": timezone.now(),
                "is_active": True,
                "metadata": {"misp_type": misp_type, "event_id": attr.get("event_id")},
            },
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return IntegrationSyncLog.objects.create(
        target=IntegrationSyncLog.Target.MISP,
        direction=IntegrationSyncLog.Direction.IMPORT,
        status=IntegrationSyncLog.Status.SUCCESS,
        message=f"Imported created={created} updated={updated}",
        records_processed=created + updated,
        details={"created": created, "updated": updated, "fetched": len(attrs)},
    )
