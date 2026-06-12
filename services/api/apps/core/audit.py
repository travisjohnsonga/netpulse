"""Central audit logging.

`log_event` is the single entry point used by views, signals and tasks to write
an AuditLog row. It is deliberately best-effort: a logging failure must never
break the action being audited.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Metadata keys whose values are credentials and must never be persisted in
# clear text on an audit record (callers pass arbitrary context dicts).
_SENSITIVE_KEYS = frozenset({
    "authorization", "cookie", "x-api-key", "api_key", "apikey",
    "token", "password", "secret",
})


def scrub_sensitive(data: dict | None) -> dict:
    """Return a copy of ``data`` with sensitive values masked, safe to persist."""
    if not isinstance(data, dict):
        return {}
    return {k: ("***REDACTED***" if str(k).lower() in _SENSITIVE_KEYS else v)
            for k, v in data.items()}


# Human-friendly labels for the device fields surfaced in audit diffs. Keys are
# Device snapshot keys (see ``snapshot_device``); anything not listed falls back
# to a title-cased version of the field name.
DEVICE_FIELD_LABELS = {
    "hostname": "Hostname",
    "management_ip": "IP Address",
    "ip_address": "IP Address",
    "platform": "Platform",
    "os_version": "OS Version",
    "model": "Model",
    "serial_number": "Serial Number",
    "site": "Site",
    "role": "Role",
    "credential_profile": "Credential Profile",
    "status": "Status",
    "is_reachable": "Reachability",
    "ip_locked": "IP Lock",
    "notes": "Notes",
}

# Snapshot keys never worth surfacing in a diff (noisy/auto-managed timestamps).
_DIFF_SKIP_FIELDS = frozenset({"updated_at", "created_at", "last_seen", "last_polled"})


def diff_model_changes(before: dict, after: dict, field_labels: dict | None = None) -> list[dict]:
    """Compare two model snapshots and return the changed fields.

    Each entry is ``{"field", "label", "before", "after"}`` with values coerced
    to ``str`` (or ``None``). Auto-managed/noisy fields are skipped. The result
    is sorted by label for a stable, readable order.
    """
    labels = field_labels or {}
    changes = []
    for field in (set(before or {}) | set(after or {})):
        if field in _DIFF_SKIP_FIELDS:
            continue
        old_val = (before or {}).get(field)
        new_val = (after or {}).get(field)
        if old_val == new_val:
            continue
        changes.append({
            "field": field,
            "label": labels.get(field, field.replace("_", " ").title()),
            "before": None if old_val is None else str(old_val),
            "after": None if new_val is None else str(new_val),
        })
    return sorted(changes, key=lambda c: c["label"])


def snapshot_device(device) -> dict:
    """Capture a device's audited fields for before/after diffing."""
    return {
        "hostname": device.hostname,
        "management_ip": str(device.management_ip or ""),
        "platform": device.platform,
        "os_version": device.os_version or "",
        "model": device.model or "",
        "serial_number": device.serial_number or "",
        "site": str(device.site) if device.site_id else None,
        "role": str(device.role) if device.role_id else None,
        "credential_profile": (
            str(device.credential_profile) if device.credential_profile_id else None
        ),
        "status": device.status,
        "is_reachable": device.is_reachable,
        "ip_locked": device.ip_locked,
        "notes": device.notes or "",
    }


def describe_changes(name: str, changes: list[dict]) -> str:
    """Human-readable one-line summary of a field-level diff for ``name``."""
    if not changes:
        return f"{name} updated"
    if len(changes) == 1:
        c = changes[0]
        return f'{name}: {c["label"]} changed from "{c["before"]}" to "{c["after"]}"'
    fields = ", ".join(c["label"] for c in changes)
    return f"{name}: {len(changes)} fields updated ({fields})"


def client_ip(request) -> str | None:
    """Best client IP from a request, honouring X-Forwarded-For (first hop)."""
    if not request:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log_event(
    event_type,
    *,
    request=None,
    user=None,
    username: str = "",
    target=None,
    description: str = "",
    metadata: dict | None = None,
    success: bool = True,
    error_message: str = "",
):
    """Record an audit event. Returns the AuditLog row, or None on failure.

    `user` defaults to the authenticated request user. `target` may be any model
    instance; its type/pk/str are snapshotted. Never raises.
    """
    try:
        from .models import AuditLog

        if request is not None and user is None:
            ru = getattr(request, "user", None)
            if ru is not None and getattr(ru, "is_authenticated", False):
                user = ru

        target_type = target_id = target_name = ""
        if target is not None:
            target_type = type(target).__name__
            target_id = str(getattr(target, "pk", "") or "")
            target_name = str(target)[:256]

        ua = ""
        if request is not None:
            ua = (request.META.get("HTTP_USER_AGENT", "") or "")[:256]

        return AuditLog.objects.create(
            event_type=event_type,
            user=user if (user and getattr(user, "pk", None)) else None,
            username=(username or (getattr(user, "username", "") if user else ""))[:150],
            ip_address=client_ip(request),
            user_agent=ua,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            description=description,
            metadata=scrub_sensitive(metadata),
            success=success,
            error_message=(error_message or "")[:512],
        )
    except Exception:  # noqa: BLE001 — auditing must never break the action
        # We never put the exception (or anything derived from it, including
        # exc_info) in the log line: the failing insert carries the audit fields
        # (description/metadata/error_message), which can hold sensitive values,
        # and a DB driver may echo them into the exception text.
        #
        # Re-derive the event type from the canonical EventType enum rather than
        # logging the caller-supplied `event_type` directly: the logged token is
        # then a trusted constant from the model, never an untrusted input value.
        safe_event = "<unknown>"
        try:
            from .models import AuditLog
            safe_event = AuditLog.EventType(event_type).value
        except Exception:  # noqa: BLE001 — unrecognised/odd value → stay generic
            pass
        logger.warning("audit log_event failed for event_type=%s", safe_event)
        return None
