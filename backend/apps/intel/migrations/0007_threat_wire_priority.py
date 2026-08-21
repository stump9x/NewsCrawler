# Generated manually for Threat.wire_priority

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0006_feedsource_consecutive_failures"),
    ]

    operations = [
        migrations.AddField(
            model_name="threat",
            name="wire_priority",
            field=models.PositiveSmallIntegerField(
                db_index=True,
                default=0,
                help_text="Higher values pin items to the top of The Wire (e.g. Vietnam-related).",
            ),
        ),
        migrations.AlterModelOptions(
            name="threat",
            options={"ordering": ["-wire_priority", "-published_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="threat",
            index=models.Index(
                fields=["wire_priority", "published_at"],
                name="intel_threa_wire_pr_idx",
            ),
        ),
    ]
