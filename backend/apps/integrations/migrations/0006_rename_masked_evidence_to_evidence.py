from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0005_githubfinding_match_lines_and_repo_index"),
    ]

    operations = [
        migrations.RenameField(
            model_name="githubfinding",
            old_name="masked_evidence",
            new_name="evidence",
        ),
    ]
