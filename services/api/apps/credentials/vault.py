"""
OpenBao (Vault-compatible) helper for credential secret material.

Secrets — passwords, SSH keys, SNMP community strings, API tokens — are stored
in OpenBao at the profile's ``vault_path`` and are **never** persisted in
PostgreSQL.  This module is the single choke point for reading/writing them.

Behaviour when OpenBao is not configured (``OPENBAO_TOKEN`` empty, e.g. local
dev and the test suite): writes/deletes become no-ops and reads return ``{}``.
Crucially, secrets are simply *discarded* in that mode — they are never written
to the relational DB — so the security invariant holds everywhere.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# OpenBao KV v2 mount point. Matches the ingest services' convention.
_MOUNT_POINT = "secret"


import json
import os

_KEYS_FILE = os.environ.get("OPENBAO_KEYS_FILE", "/openbao/data/.init_keys")


def _resolve_token() -> str:
    """
    Resolve the OpenBao token. Prefer the configured OPENBAO_TOKEN; otherwise
    fall back to the root token written by ``init_openbao`` to the shared
    openbao-data volume (file-storage mode generates a dynamic root token, so
    there is no static env token to use).
    """
    env_token = getattr(settings, "OPENBAO_TOKEN", "") or ""
    if env_token:
        return env_token
    try:
        with open(_KEYS_FILE) as fh:
            return json.load(fh).get("root_token", "") or ""
    except Exception:
        return ""


def vault_enabled() -> bool:
    """True only when a token is resolvable — otherwise we run secret-less."""
    return bool(_resolve_token())


def _client():
    import hvac  # imported lazily so the package isn't required in tests

    return hvac.Client(
        url=getattr(settings, "OPENBAO_ADDR", "http://openbao:8200"),
        token=_resolve_token(),
    )


def write_secret(path: str, data: dict) -> None:
    """
    Store ``data`` (a flat dict of secret fields) at ``path``.

    No-op when OpenBao is unconfigured — the secrets are discarded, never
    written anywhere else.
    """
    data = {k: v for k, v in data.items() if v not in (None, "")}
    if not data:
        return
    if not vault_enabled():
        logger.warning(
            "OpenBao not configured; discarding %d secret field(s) for %r",
            len(data), path,
        )
        return
    _client().secrets.kv.v2.create_or_update_secret(
        path=path, secret=data, mount_point=_MOUNT_POINT,
    )
    logger.info("wrote %d secret field(s) to %r", len(data), path)


def read_secret(path: str) -> dict:
    """Return the secret dict at ``path``; ``{}`` if missing or vault disabled."""
    if not vault_enabled():
        return {}
    try:
        resp = _client().secrets.kv.v2.read_secret_version(
            path=path, mount_point=_MOUNT_POINT,
        )
        return resp["data"]["data"]
    except Exception as exc:  # hvac.exceptions.InvalidPath, etc.
        logger.warning("could not read secret %r: %s", path, exc)
        return {}


def delete_secret(path: str) -> None:
    """Delete all versions of the secret at ``path`` (no-op if vault disabled)."""
    if not vault_enabled():
        return
    try:
        _client().secrets.kv.v2.delete_metadata_and_all_versions(
            path=path, mount_point=_MOUNT_POINT,
        )
        logger.info("deleted secret %r", path)
    except Exception as exc:
        logger.warning("could not delete secret %r: %s", path, exc)
