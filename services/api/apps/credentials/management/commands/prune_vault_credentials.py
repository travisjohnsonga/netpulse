"""Prune stale / leaked credential secrets from OpenBao.

Two classes of secret under ``secret/netpulse/credentials/{pk}`` must be removed:

  * **Orphans** — no ``CredentialProfile`` row references the pk. Because the
    vault path reuses the profile's (reusable) primary key, an orphaned secret
    is silently inherited by the next profile that lands on the same pk — which
    is exactly how real credentials appeared to "revert" after a rebuild.
  * **Leaked test fixtures** — the integration suite's real-looking fixture
    secrets (vault.TEST_FIXTURE_SECRETS) that reached a live vault before the
    isolation guards existed.

Both are deleted by default. Use ``--dry-run`` to preview, ``--orphans-only`` /
``--fixtures-only`` to narrow the scope. No-op when the vault is disabled.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.credentials import vault
from apps.credentials.models import CredentialProfile

_BASE = "netpulse/credentials"


class Command(BaseCommand):
    help = "Delete orphaned and leaked-fixture credential secrets from OpenBao."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted without deleting.",
        )
        parser.add_argument(
            "--orphans-only", action="store_true",
            help="Only prune secrets with no matching CredentialProfile.",
        )
        parser.add_argument(
            "--fixtures-only", action="store_true",
            help="Only prune secrets containing known test-fixture values.",
        )

    def handle(self, *args, **opts):
        if not vault.vault_enabled():
            self.stdout.write("OpenBao not configured / disabled — nothing to do.")
            return

        dry = opts["dry_run"]
        do_orphans = not opts["fixtures_only"]
        do_fixtures = not opts["orphans_only"]

        client = vault._client()
        try:
            keys = client.secrets.kv.v2.list_secrets(
                path=_BASE, mount_point=vault._MOUNT_POINT,
            )["data"]["keys"]
        except Exception as exc:  # InvalidPath when the tree is empty, etc.
            self.stdout.write(f"No credential secrets to scan ({exc}).")
            return

        live_pks = set(
            str(pk) for pk in CredentialProfile.objects.values_list("pk", flat=True)
        )
        pruned = kept = 0
        for key in keys:
            # list_secrets returns leaf names; sub-trees end with "/".
            if key.endswith("/"):
                continue
            path = f"{_BASE}/{key}"
            try:
                raw = client.secrets.kv.v2.read_secret_version(
                    path=path, mount_point=vault._MOUNT_POINT,
                    raise_on_deleted_version=True,
                )["data"]["data"]
            except Exception as exc:
                self.stderr.write(f"  skip {path}: read error {exc}")
                continue

            is_orphan = key not in live_pks
            fixture_fields = sorted(
                k for k, v in raw.items() if vault._is_unstorable(v)
            )
            reason = None
            if do_orphans and is_orphan:
                reason = "orphan (no profile)"
            elif do_fixtures and fixture_fields:
                reason = f"leaked fixture in {', '.join(fixture_fields)}"

            if not reason:
                kept += 1
                continue

            verb = "would delete" if dry else "deleting"
            self.stdout.write(f"  {verb} {path} — {reason}")
            if not dry:
                client.secrets.kv.v2.delete_metadata_and_all_versions(
                    path=path, mount_point=vault._MOUNT_POINT,
                )
            pruned += 1

        action = "would prune" if dry else "pruned"
        self.stdout.write(self.style.SUCCESS(
            f"Done — {action} {pruned}, kept {kept} credential secret(s)."
        ))
