# Generated manually for FeedSource.consecutive_failures

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0005_searx_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="consecutive_failures",
            field=models.PositiveSmallIntegerField(
                db_index=True,
                default=0,
                help_text="Incremented on fetch error; reset on success. Deleted after threshold.",
            ),
        ),
    ]
