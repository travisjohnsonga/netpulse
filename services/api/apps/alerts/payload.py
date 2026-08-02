"""
A channel-agnostic view of an AlertEvent for the notifiers.

`build_payload(event, transition)` flattens the event's rule + labels +
annotations into a single `AlertPayload` so every notifier (email, Teams,
webhook, …) renders from the same normalized data instead of re-digging the
JSON. Severity/title/message/device resolution mirrors AlertEventSerializer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Transition values passed to the dispatcher / notifiers.
FIRING = "firing"
RESOLVED = "resolved"

# Severity ordering for threshold comparisons (higher = more severe).
SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

_SEVERITY_EMOJI = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "🟢",
}
# Hex colours (Teams / webhook embeds want these without the leading '#').
_SEVERITY_COLOR = {
    "critical": "D32F2F", "high": "F57C00", "medium": "FBC02D",
    "low": "1976D2", "info": "388E3C",
}


@dataclass
class AlertPayload:
    event_id: int | None
    transition: str            # "firing" | "resolved"
    severity: str              # critical/high/medium/low/info
    title: str
    message: str
    device: str = ""
    device_id: int | None = None
    rule_name: str = ""
    alert_type: str = ""
    labels: dict = field(default_factory=dict)
    fired_at: str = ""         # ISO-8601
    resolved_at: str = ""      # ISO-8601 (resolved transitions only)
    resolved_by: str = ""
    resolution_note: str = ""
    link: str = ""             # back-link into the spane UI

    @property
    def is_resolved(self) -> bool:
        return self.transition == RESOLVED

    @property
    def emoji(self) -> str:
        if self.is_resolved:
            return "✅"
        return _SEVERITY_EMOJI.get(self.severity, "⚪")

    @property
    def color(self) -> str:
        if self.is_resolved:
            return "388E3C"  # green
        return _SEVERITY_COLOR.get(self.severity, "808080")

    @property
    def state_word(self) -> str:
        return "Resolved" if self.is_resolved else "Firing"

    def subject_title(self) -> str:
        """Headline without a leading emoji (cards prepend the emoji separately)."""
        if self.is_resolved:
            return f"Resolved: {self.title}"
        return f"[{self.severity.upper()}] {self.title}"

    def subject(self) -> str:
        """One-line subject/headline shared by email + cards."""
        return f"{self.emoji} {self.subject_title()}"


def _alert_link(event_id) -> str:
    from django.conf import settings

    base = (getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")
    if not base or event_id is None:
        return ""
    # The Alerts page reads ?event= to focus/expand a specific event.
    return f"{base}/alerts?event={event_id}"


def build_payload(event, transition: str) -> AlertPayload:
    """Flatten an AlertEvent + transition into an AlertPayload."""
    labels = event.labels or {}
    annotations = event.annotations or {}

    severity = (annotations.get("severity")
                or labels.get("severity")
                or (event.rule.severity if event.rule_id else "info"))
    title = annotations.get("title") or (event.rule.name if event.rule_id else "Alert")
    message = annotations.get("message") or annotations.get("description") or ""

    device = labels.get("device") or ""
    device_id = labels.get("device_id")
    if not device and device_id is not None:
        try:
            from apps.devices.models import Device
            device = (Device.objects.filter(id=device_id)
                      .values_list("hostname", flat=True).first()) or ""
        except Exception:  # noqa: BLE001 — never let payload building raise
            device = ""

    return AlertPayload(
        event_id=event.pk,
        transition=transition,
        severity=str(severity).lower(),
        title=title,
        message=message,
        device=device,
        device_id=device_id,
        rule_name=event.rule.name if event.rule_id else "",
        alert_type=annotations.get("alert_type") or labels.get("alert_type") or "",
        labels=labels,
        fired_at=event.created_at.isoformat() if getattr(event, "created_at", None) else "",
        resolved_at=event.resolved_at.isoformat() if event.resolved_at else "",
        resolved_by=event.resolved_by or "",
        resolution_note=event.resolution_note or "",
        link=_alert_link(event.pk),
    )
