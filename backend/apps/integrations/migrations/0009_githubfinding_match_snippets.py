from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0008_alter_githubscan_max_results_1500"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubfinding",
            name="match_snippets",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
