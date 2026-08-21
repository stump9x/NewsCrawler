from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("intel", "0011_feedsource_wordpress_site_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedsource",
            name="requires_tor",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Fetch via TOR_SOCKS_PROXY (.onion or clearnet hosts blocked on egress).",
            ),
        ),
    ]
