"""
Management command: init_openbao

Bootstraps a file-storage OpenBao on first run and auto-unseals on every run.

First run (uninitialised):
  - POST /v1/sys/init {secret_shares: 1, secret_threshold: 1}
  - persist the unseal key + root token to OPENBAO_KEYS_FILE (chmod 600) on the
    shared openbao-data volume
  - unseal, enable the KV-v2 engine at secret/, and create the `netpulse`
    AppRole (role_id/secret_id also written to the keys file)

Subsequent runs (initialised):
  - read the unseal key from the keys file and, if sealed, unseal

The keys file is the source of truth for the root token in this single-node
deployment (file storage cannot pin a static root token the way -dev did);
apps.credentials.vault falls back to it when OPENBAO_TOKEN is unset. The file
must never be committed (see .gitignore).

Safe to run repeatedly; network/secret errors are logged, not fatal, so the
container can still start (vault reads degrade gracefully).
"""
from __future__ import annotations

import json
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

KEYS_FILE = os.environ.get("OPENBAO_KEYS_FILE", "/openbao/data/.init_keys")
KV_MOUNT = "secret"
APPROLE_NAME = "netpulse"


class Command(BaseCommand):
    help = "Initialise (first run) and auto-unseal OpenBao; persist keys to the data volume."

    def handle(self, *args, **options):
        import requests

        addr = getattr(settings, "OPENBAO_ADDR", "http://openbao:8200")
        self.addr = addr.rstrip("/")
        self.requests = requests

        if not self._wait_reachable():
            self.stderr.write("OpenBao not reachable — skipping init/unseal (vault reads will degrade).")
            return

        status = self._seal_status()
        if status is None:
            self.stderr.write("Could not read seal status — skipping.")
            return

        if not status.get("initialized"):
            self.stdout.write("OpenBao uninitialised — initialising (1/1)…")
            keys = self._init()
            if not keys:
                return
            self._unseal(keys["unseal_key"])
            self._enable_kv(keys["root_token"])
            self._create_approle(keys["root_token"])
            self.stdout.write(self.style.SUCCESS("OpenBao initialised, unsealed and configured."))
            return

        # Already initialised — unseal if needed using the stored key.
        if status.get("sealed"):
            keys = self._load_keys()
            if not keys or not keys.get("unseal_key"):
                self.stderr.write("OpenBao is sealed but no stored unseal key was found — manual unseal required.")
                return
            self._unseal(keys["unseal_key"])
            self.stdout.write(self.style.SUCCESS("OpenBao unsealed from stored key."))
        else:
            self.stdout.write("OpenBao already initialised and unsealed.")

    # ── HTTP helpers ─────────────────────────────────────────────────────────

    def _wait_reachable(self, attempts: int = 10) -> bool:
        for _ in range(attempts):
            try:
                self.requests.get(f"{self.addr}/v1/sys/health",
                                  params={"sealedcode": 200, "uninitcode": 200}, timeout=3)
                return True
            except Exception:
                time.sleep(2)
        return False

    def _seal_status(self) -> dict | None:
        try:
            return self.requests.get(f"{self.addr}/v1/sys/seal-status", timeout=5).json()
        except Exception as exc:
            self.stderr.write(f"seal-status failed: {exc}")
            return None

    def _init(self) -> dict | None:
        try:
            resp = self.requests.put(
                f"{self.addr}/v1/sys/init",
                json={"secret_shares": 1, "secret_threshold": 1}, timeout=10,
            ).json()
            keys = {
                "unseal_key": resp["keys_base64"][0],
                "root_token": resp["root_token"],
            }
            self._save_keys(keys)
            return keys
        except Exception as exc:
            self.stderr.write(f"init failed: {exc}")
            return None

    def _unseal(self, key: str) -> None:
        try:
            self.requests.put(f"{self.addr}/v1/sys/unseal", json={"key": key}, timeout=10)
        except Exception as exc:
            self.stderr.write(f"unseal failed: {exc}")

    def _enable_kv(self, token: str) -> None:
        try:
            self.requests.post(
                f"{self.addr}/v1/sys/mounts/{KV_MOUNT}",
                headers={"X-Vault-Token": token},
                json={"type": "kv", "options": {"version": "2"}}, timeout=10,
            )
        except Exception as exc:
            self.stderr.write(f"enable kv failed: {exc}")

    def _create_approle(self, token: str) -> None:
        hdr = {"X-Vault-Token": token}
        try:
            # Enable approle auth (ignore "already enabled").
            self.requests.post(f"{self.addr}/v1/sys/auth/approle",
                               headers=hdr, json={"type": "approle"}, timeout=10)
            self.requests.post(
                f"{self.addr}/v1/auth/approle/role/{APPROLE_NAME}",
                headers=hdr, json={"token_policies": "default", "token_ttl": "1h", "token_max_ttl": "4h"}, timeout=10,
            )
            role_id = self.requests.get(
                f"{self.addr}/v1/auth/approle/role/{APPROLE_NAME}/role-id", headers=hdr, timeout=10
            ).json()["data"]["role_id"]
            secret_id = self.requests.post(
                f"{self.addr}/v1/auth/approle/role/{APPROLE_NAME}/secret-id", headers=hdr, timeout=10
            ).json()["data"]["secret_id"]
            keys = self._load_keys() or {}
            keys.update({"approle_role_id": role_id, "approle_secret_id": secret_id})
            self._save_keys(keys)
        except Exception as exc:
            self.stderr.write(f"approle setup failed (non-fatal): {exc}")

    # ── key file ─────────────────────────────────────────────────────────────

    def _save_keys(self, keys: dict) -> None:
        try:
            with open(KEYS_FILE, "w") as fh:
                json.dump(keys, fh)
            os.chmod(KEYS_FILE, 0o600)
            self.stdout.write(f"Wrote OpenBao keys to {KEYS_FILE} (chmod 600).")
        except Exception as exc:
            self.stderr.write(f"could not write keys file: {exc}")

    def _load_keys(self) -> dict | None:
        try:
            with open(KEYS_FILE) as fh:
                return json.load(fh)
        except Exception:
            return None
