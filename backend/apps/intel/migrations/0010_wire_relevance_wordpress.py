from django.db import migrations, models


RELEVANCE_TERMS = (
    "breach",
    "compromised",
    "credential",
    "csirt",
    "cyber",
    "dark web",
    "data exposure",
    "data leak",
    "ddos",
    "exploit",
    "fraud",
    "government portal",
    "hack",
    "infostealer",
    "malware",
    "phishing",
    "ransomware",
    "remote code execution",
    "scam",
    "security advisory",
    "security flaw",
    "spyware",
    "stolen data",
    "threat actor",
    "vulnerability",
    "zero-day",
    "zero day",
)


def classify_existing_rss(apps, schema_editor):
    Threat = apps.get_model("intel", "Threat")
    irrelevant_ids = []
    rows = Threat.objects.filter(source="news").only(
        "id", "title", "summary", "raw_payload"
    )
    for threat in rows.iterator(chunk_size=500):
        payload = threat.raw_payload if isinstance(threat.raw_payload, dict) else {}
        if "feed_source" not in payload:
            continue
        category = str(payload.get("category") or "news").lower()
        if category in {"cert", "breach", "ransomware"}:
            continue
        text = " ".join(
            (
                threat.title or "",
                threat.summary or "",
                str(payload.get("feed") or ""),
            )
        ).casefold()
        if "cve-" not in text and not any(term in text for term in RELEVANCE_TERMS):
            irrelevant_ids.append(threat.id)
            if len(irrelevant_ids) >= 500:
                Threat.objects.filter(pk__in=irrelevant_ids).update(
                    wire_relevant=False
                )
                irrelevant_ids.clear()
    if irrelevant_ids:
        Threat.objects.filter(pk__in=irrelevant_ids).update(wire_relevant=False)


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0009_feedsource_processing_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="is_wordpress",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="threat",
            name="wire_relevant",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="False hides off-topic RSS content from The Wire.",
            ),
        ),
        migrations.RunPython(classify_existing_rss, migrations.RunPython.noop),
    ]
