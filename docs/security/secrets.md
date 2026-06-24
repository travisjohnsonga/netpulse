# Secrets Management

Every device, integration and SSO credential is stored in OpenBao (a
Vault-compatible secret store), never in PostgreSQL or in code. PostgreSQL holds
only a **reference** — the OpenBao path — and the API never returns a secret
value once it has been written.

## OpenBao storage (hvac)

`apps/credentials/vault.py` wraps the `hvac` client and uses the KV v2 engine at
mount point `secret`:

- `write_secret(path, data)` — `create_or_update_secret`
- `read_secret(path)` — `read_secret_version`
- `delete_secret(path)` — `delete_metadata_and_all_versions`

Credential profiles store their secrets at `netpulse/credentials/{pk}`
(`CredentialProfile.default_vault_path`, `apps/credentials/models.py`); SSO
secrets at `secret/sso/{id}/credentials`; integrations under
`netpulse/integrations/...`. Only the path is persisted in the database column
`vault_path` — a non-sensitive reference.

When OpenBao is unconfigured or disabled, `write_secret` is a no-op (the secret
is discarded, never written elsewhere) and `read_secret` returns `{}`, so the
invariant "secrets never land in the DB" holds even in degraded states.

## Least-privilege AppRole

The collector secret-broker authenticates to OpenBao with a dedicated AppRole,
not the platform token. The provisioning command
`manage.py setup_secret_broker` installs a policy scoped to **read-only on
device-credential data paths, with no list capability**:

```hcl
path "secret/data/netpulse/credentials/+" {
  capabilities = ["read"]
}
```

The single `+` matches the profile PK segment only; there is no
`secret/metadata/*` (where list lives) and no broader path. A logic bug therefore
degrades to "fails to fetch," never "enumerates the vault." The broker reads with
this AppRole in `_scoped_read` (`apps/collectors/secret_broker.py`), authenticating
via `BROKER_APPROLE_ROLE_ID` / `BROKER_APPROLE_SECRET_ID`.

## Fail-closed behavior

This is the load-bearing control: **a production broker without a scoped AppRole
refuses to start, and refuses to read.**

`BROKER_REQUIRE_APPROLE` (`config/settings/base.py`) defaults to `not DEBUG` — so
it is on in production. With it set:

- `check_broker_config()` raises at startup if the AppRole env vars are absent,
  before the broker serves anything (`apps/collectors/secret_broker.py`, called
  from `run_secret_broker`):

  ```python
  if _require_approle() and not _approle_configured():
      raise RuntimeError("secret-broker refuses to start: ...")
  ```

- `_scoped_read` refuses to fall back to the platform reader even if the startup
  check were bypassed (defense in depth):

  ```python
  if _require_approle():
      raise RuntimeError("scoped AppRole required but not configured — "
                         "refusing platform-reader fallback")
  ```

The platform-reader fallback exists only for local development
(`BROKER_REQUIRE_APPROLE=false`).

`tests/test_secret_broker.py` (`TestFailClosed`) pins this behavior:
`test_prod_without_approle_refuses_to_start`,
`test_prod_without_approle_read_fails_closed`, and
`test_dev_without_approle_is_allowed`. The same file also tests broker
authorization (`TestDenials`, `TestConfusedDeputy`): identity comes from the
transport, request-body `collector_id` / `account` / `vault_path` are ignored,
the vault path must match `^netpulse/credentials/[0-9]+$`, a device owned by
another collector is denied, and a read error degrades to deny.

## Secrets are never returned or logged

**Write-only serializers.** All secret fields are `write_only=True`, so the API
accepts a secret on write but never serializes it back. `CredentialProfile`
exposes 12 such fields (`apps/credentials/serializers.py`):

```python
def _secret_field():
    return serializers.CharField(write_only=True, required=False, allow_blank=True)
```

Integration serializers follow the same pattern, returning a boolean
"is it set?" instead of the value — `password_set` (Email/SMTP), `api_token_set`
(Mist), `api_key_set` (UniFi cloud). The NetBox import serializer's `api_key` /
`api_token` are write-only.

**No secret values in logs.** `vault.py` logs only field counts and paths
(`"wrote %d secret field(s) to %r"`), never values. Audit metadata is run through
`scrub_sensitive` (see [Audit Logging](audit-logging.md)), which masks keys like
`password`, `token`, `secret`, `authorization`, and `cookie`.

**Defense-in-depth placeholder guard.** `write_secret` refuses to persist known
test/placeholder sentinels (raising `ValueError`), and `read_secret`
self-heals by dropping any such stale value — so a leaked fixture can neither be
stored in nor trusted from a real vault.
