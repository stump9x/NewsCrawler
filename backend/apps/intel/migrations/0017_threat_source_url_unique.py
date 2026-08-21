# Generated manually for Wire source_url uniqueness

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intel", "0016_document_delete_memory"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="threat",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_url", ""), _negated=True),
                fields=("source_url",),
                name="intel_threat_source_url_nonempty_uniq",
            ),
        ),
    ]
