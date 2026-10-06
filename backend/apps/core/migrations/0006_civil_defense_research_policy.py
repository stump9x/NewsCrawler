from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_wiretopicrollout"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="wirefilterprompt",
            name="civil_defense_research_prompt",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="wirefilterprompt",
            name="civil_defense_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="wirefilterprompt",
            name="civil_defense_updated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="civil_defense_prompt_updates",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="wirefilterpromptrevision",
            name="policy_type",
            field=models.CharField(
                choices=[
                    ("wire_filter", "Trạm tin tức"),
                    ("mindmap", "Mindmap"),
                    ("civil_defense", "Phòng thủ dân sự 2026"),
                ],
                default="wire_filter",
                max_length=16,
            ),
        ),
    ]
