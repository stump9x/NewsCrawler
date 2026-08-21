from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0003_mindmap_prompt_policy")]

    operations = [
        migrations.AddField(
            model_name="wirefilterprompt",
            name="favorite_recommendations_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
