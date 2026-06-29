"""PagerDuty notifier — Events API v2. A FIRING transition triggers an incident
(dedup_key = the event id so PagerDuty correlates re-sends); a RESOLVED
transition resolves it. The routing key is a secret (OpenBao)."""
from __future__ import annotations

import logging

from . import Notifier, register

logger = logging.getLogger(__name__)

_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
# PagerDuty severities: critical/error/warning/info.
_PD_SEVERITY = {
    "critical": "critical", "high": "error", "medium": "warning",
    "low": "warning", "info": "info",
}


def build_body(routing_key: str, payload) -> dict:
    dedup_key = f"spane-alert-{payload.event_id}"
    if payload.is_resolved:
        return {"routing_key": routing_key, "event_action": "resolve", "dedup_key": dedup_key}
    body = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup_key,
        "payload": {
            "summary": payload.subject_title()[:1024],
            "source": payload.device or "spane",
            "severity": _PD_SEVERITY.get(payload.severity, "warning"),
            "custom_details": {
                "message": payload.message,
                "rule": payload.rule_name,
                "device": payload.device,
                "alert_type": payload.alert_type,
            },
        },
    }
    if payload.link:
        body["links"] = [{"href": payload.link, "text": "View in spane"}]
    return body


@register("pagerduty")
class PagerDutyNotifier(Notifier):
    def send(self, channel, payload) -> tuple[bool, str]:
        from apps.alerts.channel_secrets import resolve_channel_secret

        routing_key = (resolve_channel_secret(channel, "routing_key")
                       or resolve_channel_secret(channel, "integration_key"))
        if not routing_key:
            return False, "no routing_key configured"
        try:
            import requests
            resp = requests.post(_EVENTS_URL, json=build_body(routing_key, payload), timeout=10)
            if 200 <= resp.status_code < 300:
                return True, f"enqueued ({resp.status_code})"
            return False, f"pagerduty returned {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("pagerduty notify failed for channel %s: %s", channel.pk, exc)
            return False, "request to PagerDuty failed"
