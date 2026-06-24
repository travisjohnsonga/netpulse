# Audit Logging

spane records security-relevant actions in an `AuditLog` table. Logging is
best-effort (it never breaks the action being logged) and never records secret
values.

## The `AuditLog` model

`AuditLog` (`apps/core/models.py`) captures, per event:

| Field | Notes |
|-------|-------|
| `event_type` | one of the `EventType` choices (indexed) |
| `user` | FK to the actor, nullable (`SET_NULL`) |
| `username` | snapshot of the username at event time |
| `ip_address` | client IP (from `X-Forwarded-For` first hop, else `REMOTE_ADDR`) |
| `user_agent` | request User-Agent (capped) |
| `target_type` / `target_id` / `target_name` | the affected object (indexed) |
| `description` | human-readable summary |
| `metadata` | JSON, run through `scrub_sensitive` before storage |
| `success` | outcome flag (indexed) |
| `error_message` | truncated failure text |
| `created_at` | timestamp (indexed) |

Indexing supports the common queries — composite indexes on
`(event_type, -created_at)`, `(user, -created_at)`, and
`(target_type, target_id)`.

`EventType` covers the security-relevant surface (~46 types), including
authentication (`login_success`, `login_failed`, `logout`, `password_changed`),
user/role management (`user_created`, `user_role_changed`), credentials
(`credential_created/updated/deleted/accessed`), config
(`config_pushed`, `config_backup`, `compliance_run`), integrations
(`netbox_import`, `unifi_sync`, `mist_sync`), agents (`agent_enrolled`,
`agent_revoked`), and ChatOps (`chatops_query`, `chatops_denied`).

## The `log_event` API

`log_event` (`apps/core/audit.py`) is the single entry point:

```python
def log_event(event_type, *, request=None, user=None, username="",
              target=None, description="", metadata=None,
              success=True, error_message=""):
```

Given a `request`, it fills in the actor, the client IP (`client_ip`, which reads
`X-Forwarded-For`), and the user-agent. Given a `target` model instance, it
snapshots the type, PK, and string representation. It **never raises** — any
failure is caught and logged as a generic warning (re-deriving the event type
from the trusted enum, never echoing the exception or the audit payload, which
could contain sensitive text).

## Secrets are never logged

Two layers keep secrets out of the audit trail:

- `scrub_sensitive` (`apps/core/audit.py`) masks any metadata key in
  `_SENSITIVE_KEYS` — `authorization`, `cookie`, `x-api-key`, `api_key`,
  `apikey`, `token`, `password`, `secret` — to `***REDACTED***` before the row
  is written.
- Callers pass names and IDs, not values; credential events log the profile name,
  not the secret (which lives only in OpenBao — see [Secrets](secrets.md)).

## Retention and purge

Audit rows are purged on a schedule. Retention defaults to 90 days
(`AUDIT_LOG_RETENTION_DAYS`) and is adjustable at runtime via the
`audit_log_retention_days` system setting; the scheduler's `_purge_audit_log`
task (`apps/core/management/commands/run_scheduler.py`) deletes rows older than
the cutoff daily (`AUDIT_PURGE_INTERVAL_S`, default 24h). The runtime setter
`AuditRetentionView` (`apps/core/views.py`) clamps the value to 0–3650 days and
audits the change itself.

## CSV export

The audit log can be exported to CSV. Every user-influenced cell is run through
`csv_safe` to prevent spreadsheet formula injection — see
[Input Hardening](input-hardening.md#csv-formula-injection).
