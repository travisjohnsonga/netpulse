"""Notification channels. Stage 1: email via the Django mail backend."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    """Send a plain-text alert email. Returns (ok, error). Never raises."""
    from django.conf import settings
    from django.core.mail import send_mail

    if not to_email:
        return False, "no recipient"
    from_email = getattr(settings, "EMAIL_FROM", None) or getattr(settings, "DEFAULT_FROM_EMAIL", "netpulse@localhost")
    try:
        send_mail(subject, body, from_email, [to_email], fail_silently=False)
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
