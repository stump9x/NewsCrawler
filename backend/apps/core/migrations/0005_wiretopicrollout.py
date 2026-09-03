from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0004_favorite_recommendations")]

    operations = [
        migrations.CreateModel(
            name="WireTopicRollout",
            fields=[
                ("version", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("backup_path", models.CharField(blank=True, max_length=1024)),
            ],
        ),
    ]
