"""Generic webhook notifier — POSTs a JSON alert payload to ``config.url``
(optionally with ``config.headers``). The full structured payload is sent so
downstream automation can route on any field."""
from __future__ import annotations

import logging

from . import Notifier, register

logger = logging.getLogger(__name__)


def build_body(payload) -> dict:
    return {
        "event_id": payload.event_id,
        "transition": payload.transition,
        "state": payload.state_word.lower(),
        "severity": payload.severity,
        "title": payload.title,
        "message": payload.message,
        "device": payload.device,
        "device_id": payload.device_id,
        "rule": payload.rule_name,
        "alert_type": payload.alert_type,
        "labels": payload.labels,
        "fired_at": payload.fired_at,
        "resolved_at": payload.resolved_at,
        "resolved_by": payload.resolved_by,
        "link": payload.link,
    }


@register("webhook")
class WebhookNotifier(Notifier):
    def send(self, channel, payload) -> tuple[bool, str]:
        from apps.alerts.channel_secrets import resolve_channel_secret

        url = resolve_channel_secret(channel, "url") or resolve_channel_secret(channel, "webhook_url")
        if not url:
            return False, "no url configured"
        cfg = channel.config or {}
        headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
        try:
            import requests
            resp = requests.post(url, json=build_body(payload), headers=headers, timeout=10)
            if 200 <= resp.status_code < 300:
                return True, f"posted ({resp.status_code})"
            return False, f"webhook returned {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("webhook notify failed for channel %s: %s", channel.pk, exc)
            return False, "request to the webhook failed"
