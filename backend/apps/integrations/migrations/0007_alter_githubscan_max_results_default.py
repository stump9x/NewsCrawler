from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0006_rename_masked_evidence_to_evidence"),
    ]

    operations = [
        migrations.AlterField(
            model_name="githubscan",
            name="max_results",
            field=models.PositiveSmallIntegerField(default=2000),
        ),
    ]
