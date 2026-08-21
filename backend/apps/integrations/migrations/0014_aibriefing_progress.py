from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0013_last30days_finding_title_vi"),
    ]

    operations = [
        migrations.AddField(
            model_name="aibriefing",
            name="progress",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="aibriefing",
            name="progress_pct",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
