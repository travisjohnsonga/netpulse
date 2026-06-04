"""
show_credentials — safe inspection of credential profiles.

By default it shows only non-secret metadata plus a set/missing status for each
secret field (never the value, never the length). ``--show-secrets`` reveals a
PARTIAL value (first/last 4 chars, fixed-width mask) for local verification —
enough to confirm a secret starts/ends correctly without exposing it or its
length.

# TODO: Remove or restrict before public release
# This command shows sensitive credential info
# Should require additional auth or be removed
# from production deployments
# Tracked: remove before v1.0 public release
"""
from django.core.management.base import BaseCommand

from apps.credentials.models import CredentialProfile
from apps.credentials.vault import is_placeholder, read_secret


def truncate_secret(val: str) -> str:
    """
    Partial, length-hiding view of a secret for --show-secrets. Always uses a
    fixed-width mask so the output never reveals the real length.
      ''      → '(empty)'
      'abc'   → '********'          (≤4: fully masked)
      'netmagic' → 'ne********'     (≤8: first 2 + mask)
      'ThisPassword1!' → 'This********d1!'  (first 4 + mask + last 4)
    """
    if not val:
        return "(empty)"
    if len(val) <= 4:
        return "********"
    if len(val) <= 8:
        return f"{val[:2]}********"
    return f"{val[:4]}********{val[-4:]}"


class Command(BaseCommand):
    help = "Show credential profile status (set-state by default; --show-secrets reveals partial values)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--show-secrets",
            action="store_true",
            help="Show partial secret values (first/last 4 chars, length hidden)",
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

        show = options["show_secrets"]
        if show:
            self.stdout.write(self.style.NOTICE(
                "ℹ️  --show-secrets: showing partial secret values for verification "
                "(first/last 4 chars only). Full secrets stored encrypted in OpenBao."))

        for cp in profiles:
            creds = read_secret(cp.vault_path) or {}
            protocols = cp.enabled_protocols

            self.stdout.write(f"\nProfile: {cp.name} (id={cp.id})")
            self.stdout.write(f"  Vault path: {cp.vault_path or '(none)'}")
            self.stdout.write(f"  Enabled protocols: {', '.join(protocols) or '(none)'}")

            # SSH
            self.stdout.write(f"  SSH username: {cp.ssh_username or '(none)'}")
            self._emit("SSH password", creds.get("ssh_password", ""), show)

            # SNMPv3
            self.stdout.write(f"  SNMP username: {cp.snmpv3_username or '(none)'}")
            self._emit("SNMP auth key", creds.get("snmpv3_auth_key", ""), show)
            self._emit("SNMP priv key", creds.get("snmpv3_priv_key", ""), show)

            # HTTPS / API — only when the profile actually enables it.
            if "https" in protocols:
                self.stdout.write(f"  HTTPS username: {cp.https_username or '(none)'}")
                self.stdout.write(f"  HTTPS auth type: {cp.https_auth_type or '(none)'}")
                self._emit("HTTPS password", creds.get("https_password", ""), show)
                self._emit("HTTPS bearer token", creds.get("https_token", ""), show)
                self._emit("HTTPS API key", creds.get("https_api_key", ""), show)

    def _emit(self, label: str, value: str, show_secrets: bool) -> None:
        value = value or ""
        rhs = truncate_secret(value) if show_secrets else self._status(value)
        self.stdout.write(f"  {label}: {rhs}")

    @staticmethod
    def _status(value: str) -> str:
        """Set/missing/placeholder status for a secret — never the value or length."""
        if not value:
            return "❌ missing"
        if is_placeholder(value):
            return "⚠️  placeholder (test value — re-enter real secret)"
        if len(value) < 8:
            return "⚠️  too short"
        return "✅ set"
