from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chatops", "0002_identity_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatopsconfig",
            name="nlp_provider",
            field=models.CharField(
                choices=[("none", "None (regex only)"), ("local", "Local (Ollama)"), ("api", "Hosted API")],
                default="none",
                help_text="Fallback intent classifier used only when the regex parser fails.",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="chatopsconfig",
            name="nlp_endpoint",
            field=models.URLField(
                blank=True,
                help_text="Ollama base URL (local) or messages endpoint (api). Optional.",
            ),
        ),
        migrations.AddField(
            model_name="chatopsconfig",
            name="nlp_model",
            field=models.CharField(blank=True, help_text="Model name for the NLP backend.", max_length=128),
        ),
    ]
