"""Notification channels. Stage 1: email via the Django mail backend."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """Send a plain-text alert email. Returns (ok, error). Never raises.

    Prefers the UI-configured SMTP (Settings → Integrations → Email) when it's
    enabled, otherwise falls back to the env-configured Django mail backend.
    """
    from django.conf import settings
    from django.core.mail import send_mail

    if not to_email:
        return False, "no recipient"

    connection = None
    from_email = getattr(settings, "EMAIL_FROM", None) or getattr(settings, "DEFAULT_FROM_EMAIL", "netpulse@localhost")
    try:
        from apps.integrations.email import configured_connection
        conn, frm = configured_connection()
        if conn is not None:
            connection, from_email = conn, frm
    except Exception as exc:  # noqa: BLE001 — fall back to the env backend
        logger.debug("EmailSettings unavailable, using env mail backend: %s", exc)

    try:
        send_mail(subject, body, from_email, [to_email], fail_silently=False, connection=connection)
        return True, ""
    except Exception as exc:
        logger.warning("alert email to %s failed: %s", to_email, exc)
        return False, str(exc)


def send_slack(webhook_url: str, text: str) -> tuple[bool, str]:
    """POST a message to a Slack incoming webhook. Returns (ok, error)."""
    if not webhook_url:
        return False, "no webhook url"
    try:
        import requests
        resp = requests.post(webhook_url, json={"text": text}, timeout=5)
        if resp.status_code >= 300:
            return False, f"slack returned {resp.status_code}"
        return True, ""
    except Exception as exc:
        logger.warning("slack notify failed: %s", exc)
        return False, str(exc)


def format_slack_alert(name: str, target: str, error: str = "", when: str = "") -> str:
    """🔴 *Service Down: …* block for Slack."""
    lines = [f"🔴 *{name}*", f"Host: {target}"]
    if error:
        lines.append(f"Error: {error}")
    if when:
        lines.append(f"Since: {when}")
    return "\n".join(lines)


_DISCORD_COLOR = {"critical": 0xFF0000, "high": 0xFF6600, "medium": 0xFFAA00, "low": 0x0099FF, "info": 0x00AA00}
_DISCORD_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "🟢"}


def discord_embed(title: str, description: str = "", severity: str = "info", fields: list | None = None) -> dict:
    """Build a Discord webhook payload with a single colour-coded embed."""
    emoji = _DISCORD_EMOJI.get(severity, "⚪")
    return {
        "username": "NetPulse",
        "embeds": [{
            "title": f"{emoji} {title}"[:256],
            "description": (description or "")[:2000],
            "color": _DISCORD_COLOR.get(severity, 0x808080),
            "fields": fields or [],
            "footer": {"text": "NetPulse Network Intelligence"},
        }],
    }


def send_discord(webhook_url: str, payload: dict) -> tuple[bool, str]:
    """POST an embed payload to a Discord webhook (returns 204 on success)."""
    if not webhook_url:
        return False, "no webhook url"
    try:
        import requests
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            return False, f"discord returned {resp.status_code}"
        return True, ""
    except Exception as exc:
        logger.warning("discord notify failed: %s", exc, exc_info=True)
        # Don't surface the raw exception to API clients (it can leak internals).
        return False, "request to the Discord webhook failed"
