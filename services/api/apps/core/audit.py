"""Central audit logging.

`log_event` is the single entry point used by views, signals and tasks to write
an AuditLog row. It is deliberately best-effort: a logging failure must never
break the action being audited.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


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
            metadata=metadata or {},
            success=success,
            error_message=(error_message or "")[:512],
        )
    except Exception as exc:  # noqa: BLE001 — auditing must never break the action
        logger.warning("audit log_event failed (%s): %s", event_type, exc)
        return None
