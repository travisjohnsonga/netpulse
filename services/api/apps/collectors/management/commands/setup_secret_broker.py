"""Provision the secret-broker's LEAST-PRIVILEGE OpenBao identity.

The broker gets an AppRole bound to a policy that can READ device-credential
paths and NOTHING else — explicitly NO list capability anywhere. This is the
"no service gets list access to all credentials" rule: a logic bug must degrade
to "fails to fetch," never "enumerates the vault." Safe to re-run.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

POLICY_NAME = "collector-secret-broker"
ROLE_NAME = "collector-secret-broker"

# READ on device-credential data paths only (KV v2 → secret/data/<path>).
# The single `+` matches the profile pk segment. No secret/metadata/* (that is
# where LIST lives), no broader path, no list/create/update/delete.
POLICY_HCL = '''
path "secret/data/netpulse/credentials/+" {
  capabilities = ["read"]
}
'''


class Command(BaseCommand):
    help = "Provision the secret-broker AppRole + read-only, no-list OpenBao policy."

    def handle(self, *args, **opts):
        from apps.credentials import vault

        if not vault.vault_enabled():
            self.stderr.write("OpenBao is not enabled/reachable — aborting.")
            return
        client = vault._client()

        client.sys.create_or_update_policy(name=POLICY_NAME, policy=POLICY_HCL)
        self.stdout.write(self.style.SUCCESS(
            f"policy '{POLICY_NAME}' written (read-only on secret/data/netpulse/"
            f"credentials/+, NO list)"))

        methods = client.sys.list_auth_methods()
        if "approle/" not in methods:
            client.sys.enable_auth_method("approle")
            self.stdout.write("enabled approle auth method")

        client.auth.approle.create_or_update_approle(
            role_name=ROLE_NAME,
            token_policies=[POLICY_NAME],
            token_ttl="20m",
            token_max_ttl="1h",
            secret_id_num_uses=0,
            token_num_uses=0,
        )
        role_id = client.auth.approle.read_role_id(role_name=ROLE_NAME)["data"]["role_id"]
        self.stdout.write(self.style.SUCCESS(
            f"AppRole '{ROLE_NAME}' bound to '{POLICY_NAME}'"))
        self.stdout.write(f"  BROKER_APPROLE_ROLE_ID={role_id}")
        self.stdout.write(
            "  (generate a secret_id at broker start: "
            "`bao write -f auth/approle/role/collector-secret-broker/secret-id`)")
