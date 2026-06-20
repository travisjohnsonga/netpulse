from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ChatOpsPlatform",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("platform", models.CharField(choices=[("slack", "Slack"), ("teams", "Microsoft Teams"), ("gchat", "Google Chat"), ("discord", "Discord"), ("mattermost", "Mattermost")], help_text="Chat platform this row configures (one row per platform).", max_length=20, unique=True)),
                ("enabled", models.BooleanField(default=False)),
                ("display_name", models.CharField(blank=True, max_length=128)),
                ("default_response_channel", models.CharField(blank=True, help_text="Channel id for proactive/notification responses (optional).", max_length=128)),
            ],
            options={
                "verbose_name": "ChatOps Platform",
                "verbose_name_plural": "ChatOps Platforms",
                "ordering": ["platform"],
            },
        ),
        migrations.CreateModel(
            name="ChatOpsChannel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("platform", models.CharField(choices=[("slack", "Slack"), ("teams", "Microsoft Teams"), ("gchat", "Google Chat"), ("discord", "Discord"), ("mattermost", "Mattermost")], max_length=20)),
                ("channel_id", models.CharField(max_length=128)),
                ("name", models.CharField(blank=True, max_length=128)),
                ("purpose", models.CharField(choices=[("query", "Query only"), ("notify", "Notifications only"), ("both", "Query + Notifications")], default="both", max_length=10)),
                ("enabled", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "ChatOps Channel",
                "verbose_name_plural": "ChatOps Channels",
                "ordering": ["platform", "name"],
                "unique_together": {("platform", "channel_id")},
            },
        ),
    ]
