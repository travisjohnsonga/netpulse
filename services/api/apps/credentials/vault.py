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

# Sentinel/placeholder secret values that must NEVER reach a real vault. These
# are the fixture strings used by the test suite (see tests/test_credentials.py,
# tests/integration/test_02_credentials.py, tests/integration/test_11_security.py)
# plus a few obviously-fake passwords. Historically the integration suite, if run
# against a live OpenBao (the api container mounts openbao-data and can resolve
# the root token), wrote these to ``netpulse/credentials/{pk}``. Because the
# vault path is keyed on a *reusable* primary key, the leaked fixtures survived a
# Postgres reset and were read back by a newly-created real profile reusing the
# same pk — making credentials appear to "revert" to placeholder values after a
# rebuild/restart. ``OPENBAO_DISABLED`` in the test settings is one guard; this
# set is the defense-in-depth that holds even if the settings are misconfigured.
PLACEHOLDER_SECRETS = frozenset({
    "sup3r-secret-pw",
    "authkey123",
    "privkey123",
    "auth-key-secret",
    "priv-key-secret",
    "do-not-log-this-pw-9876",
    "password",
    "secret",
})

# The integration/credential suites deliberately use *real-looking* fixture
# secrets (so they exercise the non-placeholder code path — see
# tests/test_vault_placeholders.py asserting is_placeholder("RealAuthKey-8chr")
# is False). That means they are NOT caught by is_placeholder, so if the suite
# ever runs against a live OpenBao they leak into netpulse/credentials/{pk} and,
# via pk reuse, get read back by a newly-created real profile — credentials
# "revert" to these values after a rebuild. These strings must therefore still
# be refused on write and scrubbed on read, even though they are not
# placeholders for the public API-validation contract. Keep in sync with the
# fixtures in tests/test_credentials.py and tests/integration/test_02_credentials.py.
TEST_FIXTURE_SECRETS = frozenset({
    "Sup3rRealPw-2f9a",
    "RealAuthKey-8chr",
    "RealPrivKey-8chr",
})


def is_placeholder(value) -> bool:
    """True if ``value`` is a known placeholder/test sentinel that must never be
    stored in (or trusted from) a real vault.

    Public contract used by the credential serializer to reject obviously-fake
    secrets with a 400. Intentionally does NOT include the "real-looking" test
    fixtures (those exercise the legitimate-value path); the vault layer guards
    those separately via _is_unstorable / TEST_FIXTURE_SECRETS."""
    return isinstance(value, str) and value in PLACEHOLDER_SECRETS


def _is_unstorable(value) -> bool:
    """True if ``value`` must never be persisted to / trusted from a real vault.

    Superset of is_placeholder that also covers the real-looking integration
    fixtures. This is the vault-layer choke-point check (write refusal + read
    self-heal); it is deliberately broader than the public is_placeholder so
    leaked test fixtures cannot survive a round-trip even if some path stored
    them before this guard existed."""
    return is_placeholder(value) or (
        isinstance(value, str) and value in TEST_FIXTURE_SECRETS
    )


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
    """True only when a token is resolvable — otherwise we run secret-less.

    Honors the ``OPENBAO_DISABLED`` setting so the test suite never touches a
    real OpenBao, even when run inside the api container (which mounts the
    openbao-data volume and so exposes the root token via the keys file).
    """
    if getattr(settings, "OPENBAO_DISABLED", False):
        return False
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
    # Defense-in-depth: refuse to persist placeholder/test sentinels to a real
    # vault. This is the choke point that makes the "credentials revert to
    # placeholder values" bug impossible regardless of which settings module is
    # loaded — a leaked fixture write fails loudly instead of corrupting the
    # vault. See PLACEHOLDER_SECRETS above for the full root-cause explanation.
    offending = sorted(k for k, v in data.items() if _is_unstorable(v))
    if offending:
        raise ValueError(
            f"Refusing to write placeholder credential value(s) for "
            f"{', '.join(offending)} to {path!r}. These look like test/fixture "
            f"secrets and must never be stored in OpenBao."
        )
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
            path=path, mount_point=_MOUNT_POINT, raise_on_deleted_version=True,
        )
        data = resp["data"]["data"]
    except Exception as exc:  # hvac.exceptions.InvalidPath, etc.
        logger.warning("could not read secret %r: %s", path, exc)
        return {}
    # Self-heal: if a stale placeholder/test sentinel is sitting at this path
    # (e.g. left behind by an integration-test run + pk reuse), drop it rather
    # than handing it to a poller/probe or merging it back on the next update.
    # Treated as "not configured" so it doesn't masquerade as a real secret.
    stale = sorted(k for k, v in data.items() if _is_unstorable(v))
    if stale:
        logger.warning(
            "ignoring %d placeholder secret field(s) at %r: %s — re-enter the "
            "real credential to overwrite", len(stale), path, ", ".join(stale),
        )
        data = {k: v for k, v in data.items() if not _is_unstorable(v)}
    return data


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
