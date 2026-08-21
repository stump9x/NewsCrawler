# Generated manually for FeedSource conditional-fetch cache fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0007_threat_wire_priority"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="http_etag",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="feedsource",
            name="http_last_modified",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="feedsource",
            name="last_body_sha256",
            field=models.CharField(
                blank=True,
                help_text="SHA-256 of last fetched body; skip XML parse when unchanged.",
                max_length=64,
            ),
        ),
    ]
