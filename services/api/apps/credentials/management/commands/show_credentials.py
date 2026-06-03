"""
show_credentials — safe inspection of credential profiles.

Replaces the ad-hoc scripts/check_keys.py debug shell. By default it shows only
non-secret metadata plus a set/missing status and length for each secret field
(never the value). ``--show-secrets`` reveals the actual values for local
troubleshooting and must be used deliberately.

# TODO: Remove or restrict before public release
# This command shows sensitive credential info
# Should require additional auth or be removed
# from production deployments
# Tracked: remove before v1.0 public release
"""
from django.core.management.base import BaseCommand

from apps.credentials.models import CredentialProfile
from apps.credentials.vault import is_placeholder, read_secret


class Command(BaseCommand):
    help = "Show credential profile status (lengths/set-state by default; --show-secrets reveals values)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--show-secrets",
            action="store_true",
            help="Show actual secret values (USE WITH CAUTION)",
        )
        parser.add_argument(
            "--profile-id",
            type=int,
            help="Show specific profile only",
        )

    def handle(self, *args, **options):
        profiles = CredentialProfile.objects.all().order_by("id")
        if options["profile_id"]:
            profiles = profiles.filter(id=options["profile_id"])

        if not profiles:
            self.stdout.write("No credential profiles found.")
            return

        if options["show_secrets"]:
            self.stdout.write(self.style.WARNING(
                "⚠️  --show-secrets: printing plaintext secret values."))

        for cp in profiles:
            creds = read_secret(cp.vault_path) or {}
            ssh = creds.get("ssh_password", "") or ""
            auth = creds.get("snmpv3_auth_key", "") or ""
            priv = creds.get("snmpv3_priv_key", "") or ""

            self.stdout.write(f"\nProfile: {cp.name} (id={cp.id})")
            self.stdout.write(f"  Vault path: {cp.vault_path or '(none)'}")
            self.stdout.write(f"  Enabled protocols: {', '.join(cp.enabled_protocols) or '(none)'}")
            self.stdout.write(f"  SSH username: {cp.ssh_username or '(none)'}")

            if options["show_secrets"]:
                self.stdout.write(f"  SSH password: {ssh}")
                self.stdout.write(f"  SNMP username: {cp.snmpv3_username or '(none)'}")
                self.stdout.write(f"  Auth key: {auth}")
                self.stdout.write(f"  Priv key: {priv}")
            else:
                self.stdout.write(f"  SSH password: {self._status(ssh, 8)} (len={len(ssh)})")
                self.stdout.write(f"  SNMP username: {cp.snmpv3_username or '(none)'}")
                self.stdout.write(f"  Auth key: {self._status(auth, 8)} (len={len(auth)})")
                self.stdout.write(f"  Priv key: {self._status(priv, 8)} (len={len(priv)})")

    @staticmethod
    def _status(value: str, min_len: int) -> str:
        """Set/missing/placeholder status for a secret — never the value itself."""
        if not value:
            return "❌ missing"
        if is_placeholder(value):
            return "⚠️  placeholder (test value — re-enter real secret)"
        if len(value) < min_len:
            return "⚠️  too short"
        return "✅ set"
