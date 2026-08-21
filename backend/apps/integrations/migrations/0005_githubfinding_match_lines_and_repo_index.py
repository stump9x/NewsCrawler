from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0004_githubscan_active_slot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="githubfinding",
            name="match_lines",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterModelOptions(
            name="githubfinding",
            options={"ordering": ["is_text_file", "-score", "-id"]},
        ),
        migrations.AddIndex(
            model_name="githubfinding",
            index=models.Index(
                fields=["scan", "repository", "is_text_file", "-score"],
                name="integratio_scan_id_repo_txt_idx",
            ),
        ),
    ]
