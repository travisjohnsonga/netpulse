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
