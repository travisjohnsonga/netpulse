"""Idempotently provision the OpenBao PKI hierarchy for collector mTLS certs.

Creates (if absent):
  - a root PKI engine (COLLECTOR_PKI_ROOT_MOUNT) with an internal root CA,
  - an intermediate PKI engine (COLLECTOR_PKI_MOUNT) whose CSR is signed by the
    root and set back as the signed intermediate,
  - a `collector` role that issues short-lived CLIENT certs for *.netpulse.

The intermediate is what enrollment issues per-collector leaf certs from (see
apps.collectors.pki.issue_collector_cert). Safe to re-run.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Enable + configure the OpenBao PKI engines/role for collector mTLS certs."

    def handle(self, *args, **opts):
        from apps.credentials import vault

        if not vault.vault_enabled():
            self.stderr.write("OpenBao is not enabled/reachable — aborting.")
            return

        client = vault._client()
        root = settings.COLLECTOR_PKI_ROOT_MOUNT
        inter = settings.COLLECTOR_PKI_MOUNT
        role = settings.COLLECTOR_PKI_ROLE

        mounts = client.sys.list_mounted_secrets_engines()
        mounted = {p.rstrip("/") for p in mounts}

        # 1) Root engine + self-signed root CA.
        if root not in mounted:
            client.sys.enable_secrets_engine("pki", path=root,
                                             config={"max_lease_ttl": "87600h"})
            client.secrets.pki.generate_root(
                type="internal", common_name="spane collector root ca",
                mount_point=root, extra_params={"ttl": "87600h"})
            self.stdout.write(self.style.SUCCESS(f"root PKI '{root}' + root CA created"))
        else:
            self.stdout.write(f"root PKI '{root}' already present")

        # 2) Intermediate engine, CSR → signed by root → set signed.
        if inter not in mounted:
            client.sys.enable_secrets_engine("pki", path=inter,
                                             config={"max_lease_ttl": "43800h"})
            csr = client.secrets.pki.generate_intermediate(
                type="internal", common_name="spane collector intermediate ca",
                mount_point=inter)["data"]["csr"]
            signed = client.secrets.pki.sign_intermediate(
                csr=csr, common_name="spane collector intermediate ca",
                mount_point=root, extra_params={"ttl": "43800h"})["data"]["certificate"]
            client.secrets.pki.set_signed_intermediate(certificate=signed, mount_point=inter)
            self.stdout.write(self.style.SUCCESS(
                f"intermediate PKI '{inter}' created + signed by '{root}'"))
        else:
            self.stdout.write(f"intermediate PKI '{inter}' already present")

        # 3) Issuing role — short-lived CLIENT certs only.
        client.secrets.pki.create_or_update_role(
            name=role, mount_point=inter,
            extra_params={
                "allowed_domains": ["netpulse"],
                "allow_subdomains": True,
                "allow_bare_domains": True,
                "client_flag": True,
                "server_flag": False,
                "key_type": "rsa",
                "key_bits": 2048,
                "max_ttl": settings.COLLECTOR_CERT_TTL,
                "no_store": False,
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"role '{role}' on '{inter}' ready (client certs, *.netpulse, max_ttl="
            f"{settings.COLLECTOR_CERT_TTL})"))
