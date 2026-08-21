from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0010_wire_relevance_wordpress"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="wordpress_site_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
    ]
