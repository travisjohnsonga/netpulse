"""
Data migration: move each UniFi controller's legacy username/password onto a
CredentialProfile (the system the rest of NetPulse uses).

For every controller that still has a username + a password stored at the old
OpenBao path (netpulse/integrations/unifi/{id}) and no credential_profile yet:
  - create a CredentialProfile "{name} credentials" with HTTPS enabled,
  - copy the password to the profile's vault path under "https_password",
  - link the controller to it, and
  - delete the old secret.

All OpenBao access is best-effort: if the vault is unavailable/disabled (e.g.
the test suite), the schema change still applies and controllers are left
unlinked (an admin can assign a profile in the UI). Reverse is a no-op.
"""
from django.db import migrations


def forward(apps, schema_editor):
    UnifiController = apps.get_model("integrations", "UnifiController")
    CredentialProfile = apps.get_model("credentials", "CredentialProfile")

    try:
        from apps.credentials import vault
    except Exception:  # noqa: BLE001
        return

    for c in UnifiController.objects.filter(credential_profile__isnull=True):
        username = (c.username or "").strip()
        if not username:
            continue
        old_path = f"netpulse/integrations/unifi/{c.id}"
        try:
            password = (vault.read_secret(old_path) or {}).get("password", "") or ""
        except Exception:  # noqa: BLE001
            password = ""
        if not password:
            continue

        profile = CredentialProfile.objects.create(
            name=f"{c.name} credentials",
            description="Auto-migrated from UniFi controller credentials.",
            https_enabled=True,
            https_auth_type="basic",
            https_username=username,
            https_port=c.port or 443,
            https_verify_tls=bool(c.verify_ssl),
        )
        profile.vault_path = f"netpulse/credentials/{profile.pk}"
        profile.save(update_fields=["vault_path"])

        try:
            vault.write_secret(profile.vault_path, {"https_password": password})
            vault.delete_secret(old_path)
        except Exception:  # noqa: BLE001 — leave the old secret if cleanup fails
            pass

        c.credential_profile = profile
        c.save(update_fields=["credential_profile"])


def reverse(apps, schema_editor):
    # One-way: we don't move credentials back onto the controller.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0009_unificontroller_credential_profile_and_more"),
        ("credentials", "0004_sitecredential"),
    ]

    operations = [migrations.RunPython(forward, reverse)]
