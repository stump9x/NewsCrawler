"""Watch-rule matching — Watcher-style keyword alerts over ingested intel."""

from __future__ import annotations

from apps.intel.models import AlertNotification, DataLeak, Indicator, Threat, WatchRule

SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _contains(haystack: str, needle: str, *, case_sensitive: bool) -> bool:
    if not needle:
        return False
    if case_sensitive:
        return needle in (haystack or "")
    return needle.lower() in (haystack or "").lower()


def _severity_ok(item_severity: str, min_severity: str) -> bool:
    return SEVERITY_RANK.get(item_severity, 0) >= SEVERITY_RANK.get(min_severity, 0)


def match_threat_against_rules(threat: Threat) -> list[AlertNotification]:
    rules = WatchRule.objects.filter(is_active=True).filter(
        target__in=[WatchRule.Target.THREATS, WatchRule.Target.ALL]
    )
    blob = f"{threat.title}\n{threat.summary}\n{' '.join(threat.cve_ids or [])}"
    created: list[AlertNotification] = []
    for rule in rules:
        if not _severity_ok(threat.severity, rule.min_severity):
            continue
        if not _contains(blob, rule.keyword, case_sensitive=rule.case_sensitive):
            continue
        # Dedupe: same rule+threat unread
        exists = AlertNotification.objects.filter(
            rule=rule, threat=threat, is_read=False
        ).exists()
        if exists:
            continue
        note = AlertNotification.objects.create(
            rule=rule,
            title=f"Watch hit: {rule.keyword}",
            message=f"Threat matched rule '{rule.name}': {threat.title}",
            severity=threat.severity,
            threat=threat,
            recipient=rule.created_by,
        )
        created.append(note)
    return created


def match_leak_against_rules(leak: DataLeak) -> list[AlertNotification]:
    return match_leaks_against_rules([leak])


def match_leaks_against_rules(
    leaks: list[DataLeak],
) -> list[AlertNotification]:
    """Match a leak batch with O(1) rule/existing queries and one bulk insert."""
    if not leaks:
        return []
    rules = list(
        WatchRule.objects.filter(is_active=True).filter(
            target__in=[WatchRule.Target.LEAKS, WatchRule.Target.ALL]
        )
    )
    if not rules:
        return []
    leak_ids = [leak.id for leak in leaks if leak.id]
    existing = set(
        AlertNotification.objects.filter(
            leak_id__in=leak_ids,
            rule_id__in=[rule.id for rule in rules],
            is_read=False,
        ).values_list("rule_id", "leak_id")
    )
    pending: list[AlertNotification] = []
    for leak in leaks:
        blob = (
            f"{leak.title}\n{leak.description}\n{leak.affected_domain}\n"
            f"{leak.affected_organization}"
        )
        for rule in rules:
            if (rule.id, leak.id) in existing:
                continue
            if not _severity_ok(leak.severity, rule.min_severity):
                continue
            if not _contains(blob, rule.keyword, case_sensitive=rule.case_sensitive):
                continue
            pending.append(
                AlertNotification(
                    rule=rule,
                    title=f"Watch hit: {rule.keyword}",
                    message=f"Leak matched rule '{rule.name}': {leak.title}",
                    severity=leak.severity,
                    leak=leak,
                    recipient=rule.created_by,
                )
            )
    return AlertNotification.objects.bulk_create(pending, batch_size=200)


def match_indicator_against_rules(indicator: Indicator) -> list[AlertNotification]:
    rules = WatchRule.objects.filter(is_active=True).filter(
        target__in=[WatchRule.Target.INDICATORS, WatchRule.Target.ALL]
    )
    blob = f"{indicator.value}\n{indicator.description}\n{indicator.source}"
    created: list[AlertNotification] = []
    for rule in rules:
        if not _contains(blob, rule.keyword, case_sensitive=rule.case_sensitive):
            continue
        if AlertNotification.objects.filter(
            rule=rule, indicator=indicator, is_read=False
        ).exists():
            continue
        created.append(
            AlertNotification.objects.create(
                rule=rule,
                title=f"Watch hit: {rule.keyword}",
                message=f"Indicator matched rule '{rule.name}': {indicator.ioc_type}:{indicator.value}",
                severity=AlertNotification.Severity.MEDIUM,
                indicator=indicator,
                recipient=rule.created_by,
            )
        )
    return created
