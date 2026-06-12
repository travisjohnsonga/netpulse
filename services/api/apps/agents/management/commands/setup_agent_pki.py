"""Initialise the OpenBao PKI secrets engine used to sign agent certificates.

Idempotent — safe to run on every startup (wired into entrypoint.sh). Creates
the ``pki`` mount, a self-signed root CA ("spane agent ca"), the ``agent``
signing role, and the ``netpulse-agent-pki`` least-privilege policy. No-ops
cleanly when OpenBao is disabled/unconfigured or sealed, so it never blocks the
api container from starting.

Mount/role names follow settings.AGENT_PKI_MOUNT / AGENT_PKI_ROLE (default
``pki`` / ``agent``), matching apps.agents.pki.issue_agent_certificate().
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

CA_COMMON_NAME = "spane agent ca"
CA_TTL = "87600h"   # 10y root
ROLE_TTL = "8760h"  # 1y agent certs

# Least-privilege: enrollment only signs/issues agent certs and reads the CA.
AGENT_PKI_POLICY = """
path "{mount}/sign/{role}" {{
  capabilities = ["create", "update"]
}}
path "{mount}/issue/{role}" {{
  capabilities = ["create", "update"]
}}
path "{mount}/ca" {{
  capabilities = ["read"]
}}
path "{mount}/ca/pem" {{
  capabilities = ["read"]
}}
"""


class Command(BaseCommand):
    help = "Initialise OpenBao PKI (mount + root CA + agent role + policy) for agent certs."

    def handle(self, *args, **options):
        from apps.credentials import vault

        mount = getattr(settings, "AGENT_PKI_MOUNT", "pki")
        role = getattr(settings, "AGENT_PKI_ROLE", "agent")

        if not vault.vault_enabled():
            self.stdout.write("OpenBao not configured — skipping agent PKI setup.")
            return
        try:
            client = vault._client()
            if client.sys.is_sealed():
                self.stdout.write("OpenBao is sealed — skipping agent PKI setup.")
                return
        except Exception as exc:  # noqa: BLE001 — startup must not be blocked
            self.stderr.write(f"OpenBao unreachable ({exc}); skipping agent PKI setup.")
            return

        try:
            self._setup(client, mount, role)
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"Agent PKI setup failed: {exc}")
            return
        self._publish_ca_file(client, mount)
        self.stdout.write(self.style.SUCCESS("Agent PKI ready."))

    def _publish_ca_file(self, client, mount):
        """Write the CA PEM to settings.AGENT_CA_FILE (shared ssl-certs volume)
        so nginx can use it as ssl_client_certificate. Best-effort."""
        ca_file = getattr(settings, "AGENT_CA_FILE", "")
        if not ca_file:
            return
        try:
            pem = client.secrets.pki.read_ca_certificate(mount_point=mount) or ""
            if not pem.strip():
                self.stderr.write("CA PEM empty — not writing CA file.")
                return
            import os
            os.makedirs(os.path.dirname(ca_file), exist_ok=True)
            with open(ca_file, "w") as fh:
                fh.write(pem if pem.endswith("\n") else pem + "\n")
            self.stdout.write(f"CA cert written to {ca_file} (for nginx mTLS).")
        except Exception as exc:  # noqa: BLE001 — file publish is best-effort
            self.stderr.write(f"Could not write CA file (continuing): {exc}")

    # ── steps ──────────────────────────────────────────────────────────────
    def _setup(self, client, mount, role):
        # 1. Enable the PKI engine at <mount> if not already mounted.
        mounts = client.sys.list_mounted_secrets_engines()
        mounts = mounts.get("data", mounts)
        if f"{mount}/" not in mounts:
            client.sys.enable_secrets_engine(backend_type="pki", path=mount)
            self.stdout.write(f"PKI engine enabled at '{mount}/'.")
        else:
            self.stdout.write(f"PKI engine already enabled at '{mount}/'.")

        # 2. Allow long-lived (10y) root + 1y leaf certs.
        client.sys.tune_mount_configuration(path=mount, max_lease_ttl=CA_TTL)

        # 3. Generate the root CA once (read_ca_certificate returns '' when none).
        ca = ""
        try:
            ca = client.secrets.pki.read_ca_certificate(mount_point=mount) or ""
        except Exception:  # noqa: BLE001 — treat any read error as "no CA yet"
            ca = ""
        if ca.strip():
            self.stdout.write("Root CA already present.")
        else:
            result = client.secrets.pki.generate_root(
                type="internal",
                common_name=CA_COMMON_NAME,
                extra_params={"ttl": CA_TTL, "key_type": "ec", "key_bits": 384},
                mount_point=mount,
            )
            serial = ((result or {}).get("data") or {}).get("serial_number", "?")
            self.stdout.write(f"Root CA generated (serial {serial}).")

        # 4. Publish issuing-cert + CRL URLs (best-effort; non-fatal).
        addr = getattr(settings, "OPENBAO_ADDR", "http://openbao:8200").rstrip("/")
        try:
            client.secrets.pki.set_urls(
                {
                    "issuing_certificates": [f"{addr}/v1/{mount}/ca"],
                    "crl_distribution_points": [f"{addr}/v1/{mount}/crl"],
                },
                mount_point=mount,
            )
        except Exception as exc:  # noqa: BLE001
            self.stderr.write(f"Could not set PKI URLs (continuing): {exc}")

        # 5. Agent signing role — arbitrary hostnames + IP SANs, EC P-384, 1y.
        # use_csr_*=false makes the server's CN/SANs authoritative (the CSR is
        # only proof-of-key-possession), so issue_agent_certificate's common_name
        # / alt_names / ip_sans actually take effect.
        client.secrets.pki.create_or_update_role(
            name=role,
            extra_params={
                "allow_any_name": True,
                "allow_ip_sans": True,
                "allow_localhost": True,
                "key_type": "ec",
                "key_bits": 384,
                "max_ttl": ROLE_TTL,
                "ttl": ROLE_TTL,
                "require_cn": False,
                "use_csr_common_name": False,
                "use_csr_sans": False,
            },
            mount_point=mount,
        )
        self.stdout.write(f"Agent PKI role '{role}' configured.")

        # 6. Least-privilege policy for the enrollment token/identity.
        client.sys.create_or_update_policy(
            name="netpulse-agent-pki",
            policy=AGENT_PKI_POLICY.format(mount=mount, role=role),
        )
        self.stdout.write("Policy 'netpulse-agent-pki' written.")
