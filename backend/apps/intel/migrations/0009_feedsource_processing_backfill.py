# Generated manually for RSS processing policy and sitemap watermark.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0008_feedsource_conditional_cache"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="processing_version",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="RSS parser/policy version last applied to this feed body.",
            ),
        ),
        migrations.AddField(
            model_name="feedsource",
            name="sitemap_last_scanned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
