"""Slack notifier — POSTs a colour-coded attachment to an incoming webhook."""
from __future__ import annotations

import logging

from . import Notifier, register

logger = logging.getLogger(__name__)

_SLACK_COLOR = {
    "critical": "#D32F2F", "high": "#F57C00", "medium": "#FBC02D",
    "low": "#1976D2", "info": "#388E3C",
}


def build_body(payload) -> dict:
    color = "#388E3C" if payload.is_resolved else _SLACK_COLOR.get(payload.severity, "#808080")
    fields = []
    if payload.device:
        fields.append({"title": "Device", "value": payload.device, "short": True})
    fields.append({"title": "Severity", "value": payload.severity.upper(), "short": True})
    fields.append({"title": "State", "value": payload.state_word, "short": True})
    text = payload.message or ""
    if payload.link:
        text = (text + f"\n<{payload.link}|View in spane>").strip()
    return {
        "text": f"{payload.emoji} {payload.subject_title()}",
        "attachments": [{
            "color": color,
            "title": payload.title,
            "text": text,
            "fields": fields,
            "footer": "spane",
        }],
    }


@register("slack")
class SlackNotifier(Notifier):
    def send(self, channel, payload) -> tuple[bool, str]:
        from apps.alerts.channel_secrets import resolve_channel_secret

        url = resolve_channel_secret(channel, "webhook_url")
        if not url:
            return False, "no webhook_url configured"
        try:
            import requests
            resp = requests.post(url, json=build_body(payload), timeout=10)
            if 200 <= resp.status_code < 300:
                return True, f"posted ({resp.status_code})"
            return False, f"slack returned {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("slack notify failed for channel %s: %s", channel.pk, exc)
            return False, "request to the Slack webhook failed"
