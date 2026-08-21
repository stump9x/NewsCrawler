# Generated for SearxNG leak hunting (WatchRule.Target.SEARX + DataLeak.Source.SEARX)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0004_feed_sources"),
    ]

    operations = [
        migrations.AlterField(
            model_name="watchrule",
            name="target",
            field=models.CharField(
                choices=[
                    ("threats", "Threats / The Wire"),
                    ("leaks", "Data Leaks"),
                    ("indicators", "Indicators"),
                    ("searx", "Searx leak search"),
                    ("all", "All intel"),
                ],
                db_index=True,
                default="all",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="dataleak",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("hudson_rock", "Hudson Rock"),
                    ("proxynova", "ProxyNova"),
                    ("breachdirectory", "BreachDirectory"),
                    ("pastebin", "Pastebin"),
                    ("github", "GitHub"),
                    ("gitlab", "GitLab"),
                    ("bitbucket", "Bitbucket"),
                    ("stackoverflow", "StackOverflow"),
                    ("npm", "npm Registry"),
                    ("searx", "SearxNG"),
                    ("other", "Other"),
                ],
                db_index=True,
                default="manual",
                max_length=32,
            ),
        ),
    ]
