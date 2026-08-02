"""
Microsoft Teams notifier — POSTs a card to an incoming-webhook URL.

Two payload shapes (``config.card_format``):
  - "adaptive" (default): an Adaptive Card wrapped for the modern Teams
    *Workflows* / Power Automate "When a Teams webhook request is received"
    trigger — the format new Teams webhooks expect.
  - "messagecard": the legacy Office 365 connector MessageCard.

Incoming webhooks are fire-and-forget (no message id is returned), so a
RESOLVED transition posts a fresh green recovery card rather than editing the
original. The webhook URL is a secret → resolved from OpenBao (see
channel_secrets); ``config.webhook_url`` is the dev/test fallback.
"""
from __future__ import annotations

import logging

from . import Notifier, register

logger = logging.getLogger(__name__)

# Adaptive Card container / text colours by severity (resolved overrides green).
_ADAPTIVE_COLOR = {
    "critical": "Attention", "high": "Attention", "medium": "Warning",
    "low": "Accent", "info": "Accent",
}


def _facts(payload) -> list[dict]:
    facts = []
    if payload.device:
        facts.append(("Device", payload.device))
    facts.append(("Severity", payload.severity.upper()))
    facts.append(("State", payload.state_word))
    if payload.rule_name:
        facts.append(("Rule", payload.rule_name))
    if payload.is_resolved:
        if payload.resolved_at:
            facts.append(("Resolved at", payload.resolved_at))
        if payload.resolved_by:
            facts.append(("Resolved by", payload.resolved_by))
    elif payload.fired_at:
        facts.append(("Fired at", payload.fired_at))
    return facts


def build_adaptive_card(payload) -> dict:
    accent = "Good" if payload.is_resolved else _ADAPTIVE_COLOR.get(payload.severity, "Default")
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder",
         "text": f"{payload.emoji} {payload.subject_title()}", "color": accent, "wrap": True},
    ]
    if payload.message:
        body.append({"type": "TextBlock", "text": payload.message, "wrap": True, "spacing": "Small"})
    body.append({"type": "FactSet",
                 "facts": [{"title": k, "value": str(v)} for k, v in _facts(payload)]})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if payload.link:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "View in spane", "url": payload.link}
        ]
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


def build_message_card(payload) -> dict:
    section = {
        "activityTitle": f"{payload.emoji} {payload.subject_title()}",
        "facts": [{"name": k, "value": str(v)} for k, v in _facts(payload)],
        "markdown": True,
    }
    if payload.message:
        section["text"] = payload.message
    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": payload.color,
        "summary": payload.subject(),
        "title": f"{payload.emoji} {payload.subject_title()}",
        "sections": [section],
    }
    if payload.link:
        card["potentialAction"] = [{
            "@type": "OpenUri", "name": "View in spane",
            "targets": [{"os": "default", "uri": payload.link}],
        }]
    return card


@register("teams")
class TeamsNotifier(Notifier):
    def send(self, channel, payload) -> tuple[bool, str]:
        from apps.alerts.channel_secrets import resolve_channel_secret

        webhook_url = resolve_channel_secret(channel, "webhook_url")
        if not webhook_url:
            return False, "no webhook_url configured"
        cfg = channel.config or {}
        fmt = (cfg.get("card_format") or "adaptive").lower()
        body = build_message_card(payload) if fmt == "messagecard" else build_adaptive_card(payload)
        try:
            import requests
            resp = requests.post(webhook_url, json=body, timeout=10)
            # Classic connectors return 200 "1"; Workflows return 200/202; some
            # return 204. Treat any 2xx as success.
            if 200 <= resp.status_code < 300:
                return True, f"posted ({resp.status_code})"
            return False, f"teams returned {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("teams notify failed for channel %s: %s", channel.pk, exc)
            return False, "request to the Teams webhook failed"
