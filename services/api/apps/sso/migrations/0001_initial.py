from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SSOProvider",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("provider", models.CharField(choices=[
                    ("google-oauth2", "Google Workspace"),
                    ("azuread-tenant-oauth2", "Microsoft Azure AD"),
                    ("okta-oauth2", "Okta"),
                    ("github", "GitHub"),
                    ("saml", "SAML 2.0"),
                    ("ldap", "LDAP / Active Directory"),
                ], max_length=40)),
                ("client_id", models.CharField(blank=True, max_length=255)),
                ("vault_path", models.CharField(blank=True, max_length=255)),
                ("tenant_id", models.CharField(blank=True, max_length=255)),
                ("okta_domain", models.CharField(blank=True, max_length=255)),
                ("saml_metadata_url", models.URLField(blank=True)),
                ("is_enabled", models.BooleanField(default=True)),
                ("is_default", models.BooleanField(default=False)),
                ("allow_signup", models.BooleanField(default=True)),
                ("default_role", models.CharField(default="viewer", max_length=10)),
                ("allowed_domains", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
    ]
