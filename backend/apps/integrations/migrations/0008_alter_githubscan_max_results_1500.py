from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0007_alter_githubscan_max_results_default"),
    ]

    operations = [
        migrations.AlterField(
            model_name="githubscan",
            name="max_results",
            field=models.PositiveSmallIntegerField(default=1500),
        ),
    ]
