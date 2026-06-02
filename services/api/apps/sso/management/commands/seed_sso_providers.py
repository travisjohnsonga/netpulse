"""
Seed SSOProvider rows from SOCIAL_AUTH_* environment variables.

Runs from the api entrypoint (like seed_alert_rules). Idempotent: only creates a
provider when its OAuth key env var is set and a row for that provider type
doesn't already exist — so an operator who configures Azure AD credentials in
.env gets a ready-to-use provider automatically, while UI-managed providers and
admin edits are never clobbered. The client_secret goes to OpenBao (best-effort;
the dynamic backend also falls back to the static SOCIAL_AUTH_* env secret).
"""
import os

from django.core.management.base import BaseCommand

from apps.sso.models import SSOProvider

# provider type → (display name, env prefix, extra-field env→model map)
_SEEDS = [
    ("azuread-tenant-oauth2", "Microsoft Azure AD",
     "SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2",
     {"tenant_id": "SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_TENANT_ID"}),
    ("okta-oauth2", "Okta", "SOCIAL_AUTH_OKTA_OAUTH2", {}),
    ("github", "GitHub", "SOCIAL_AUTH_GITHUB", {}),
    ("google-oauth2", "Google Workspace", "SOCIAL_AUTH_GOOGLE_OAUTH2", {}),
]


class Command(BaseCommand):
    help = "Create SSOProvider rows from SOCIAL_AUTH_* env vars (idempotent)."

    def handle(self, *args, **options):
        for provider, name, prefix, extra_env in _SEEDS:
            key = os.environ.get(f"{prefix}_KEY", "").strip()
            if not key:
                continue
            if SSOProvider.objects.filter(provider=provider).exists():
                self.stdout.write(f"SSO provider {provider!r} already exists; leaving as-is")
                continue

            extras = {field: os.environ.get(env, "").strip() for field, env in extra_env.items()}
            obj = SSOProvider.objects.create(
                name=name, provider=provider, client_id=key, is_enabled=True, **extras)
            obj.vault_path = obj.default_vault_path()
            obj.save(update_fields=["vault_path"])

            secret = os.environ.get(f"{prefix}_SECRET", "").strip()
            if secret:
                try:
                    from apps.credentials import vault
                    vault.write_secret(obj.vault_path, {"client_secret": secret})
                except Exception as exc:  # noqa: BLE001 — env static secret still works
                    self.stderr.write(f"  (could not write {provider} secret to OpenBao: {exc})")
            self.stdout.write(self.style.SUCCESS(f"Seeded SSO provider: {name} ({provider})"))
