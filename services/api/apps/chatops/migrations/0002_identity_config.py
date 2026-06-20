from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chatops", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatOpsConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("allow_unmapped_read", models.BooleanField(default=True, help_text="Allow read-only queries from chat users with no linked spane account.")),
                ("require_approved_channel", models.BooleanField(default=False, help_text="Only answer queries from channels on the approved allow-list.")),
            ],
            options={
                "verbose_name": "ChatOps Config",
                "verbose_name_plural": "ChatOps Config",
            },
        ),
        migrations.CreateModel(
            name="ChatOpsIdentity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("platform", models.CharField(choices=[("slack", "Slack"), ("teams", "Microsoft Teams"), ("gchat", "Google Chat"), ("discord", "Discord"), ("mattermost", "Mattermost")], max_length=20)),
                ("platform_user_id", models.CharField(help_text="Stable per-platform user id (e.g. Slack U…).", max_length=128)),
                ("platform_user_name", models.CharField(blank=True, max_length=128)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="chatops_identities", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "ChatOps Identity",
                "verbose_name_plural": "ChatOps Identities",
                "ordering": ["platform", "platform_user_name"],
                "unique_together": {("platform", "platform_user_id")},
            },
        ),
    ]
