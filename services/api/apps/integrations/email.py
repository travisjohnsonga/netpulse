"""
Outbound SMTP email driven by the DB-backed EmailSettings (Settings →
Integrations → Email). The password is read from OpenBao at send time; a Django
SMTP connection is built dynamically from the stored settings.

Provider presets auto-fill host/port/TLS and carry setup help for the UI.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Per-provider connection defaults + UI help. 'custom' is the blank baseline.
PROVIDER_PRESETS: dict[str, dict] = {
    "gmail": {
        "host": "smtp.gmail.com", "port": 587, "use_tls": True, "use_ssl": False,
        "help": ("Gmail requires an App Password, not your normal password. Enable 2FA at "
                 "myaccount.google.com, then create one at myaccount.google.com/apppasswords "
                 "and use it here."),
    },
    "m365": {
        "host": "smtp.office365.com", "port": 587, "use_tls": True, "use_ssl": False,
        "help": ("Use your Microsoft 365 email + password. SMTP AUTH must be enabled in the "
                 "M365 admin center (admin.microsoft.com → Settings → Org settings → Mail flow)."),
    },
    "sendgrid": {
        "host": "smtp.sendgrid.net", "port": 587, "use_tls": True, "use_ssl": False,
        "username": "apikey",
        "help": 'Username is always "apikey"; the password is your SendGrid API key.',
    },
    "mailgun": {
        "host": "smtp.mailgun.org", "port": 587, "use_tls": True, "use_ssl": False,
        "help": "Use the SMTP credentials from your Mailgun dashboard (Sending → Domain settings).",
    },
    "custom": {
        "host": "", "port": 587, "use_tls": True, "use_ssl": False,
        "help": "Enter your provider's SMTP host, port and credentials.",
    },
}


def get_smtp_password() -> str:
    """Read the SMTP password from OpenBao ('' if unset/unavailable)."""
    from apps.credentials import vault
    from .models import SMTP_VAULT_PATH
    try:
        secrets = vault.read_secret(SMTP_VAULT_PATH) or {}
        return secrets.get("password", "") or ""
    except Exception as exc:  # noqa: BLE001 — never raise from the mail path
        logger.warning("could not read SMTP password from OpenBao: %s", exc)
        return ""


def _from_address(config) -> str:
    addr = config.from_email or "netpulse@localhost"
    return f"{config.from_name} <{addr}>" if config.from_name else addr


def _connection(config, password: str):
    from django.core.mail import get_connection
    return get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=config.host, port=config.port,
        username=config.username, password=password,
        use_tls=config.use_tls, use_ssl=config.use_ssl,
    )


def configured_connection():
    """
    Return (connection, from_email) when EmailSettings is enabled, else
    (None, None) so callers can fall back to the env-configured Django backend.
    """
    from .models import EmailSettings
    config = EmailSettings.objects.first()
    if not config or not config.enabled or not config.host:
        return None, None
    return _connection(config, get_smtp_password()), _from_address(config)


def send_alert_email(to_addresses, subject: str, body: str, html_body: str | None = None) -> bool:
    """
    Send an email via the configured EmailSettings. Returns False (best-effort,
    no raise) when email isn't configured/enabled or sending fails.
    """
    from .models import EmailSettings

    recipients = [a for a in (to_addresses or []) if a]
    if not recipients:
        return False
    config = EmailSettings.objects.first()
    if not config or not config.enabled or not config.host:
        logger.debug("email not configured/enabled — skipping send")
        return False
    try:
        from django.core.mail import send_mail
        send_mail(
            subject=subject, message=body, from_email=_from_address(config),
            recipient_list=recipients, html_message=html_body,
            connection=_connection(config, get_smtp_password()),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_alert_email failed: %s", exc)
        return False


def send_test_email(to_address: str) -> tuple[bool, str]:
    """Send a test email using the (possibly unsaved-but-enabled) settings.

    Returns (ok, error). Raises nothing; surfaces the SMTP error string so the UI
    can show it.
    """
    from .models import EmailSettings

    if not to_address:
        return False, "no recipient"
    config = EmailSettings.objects.first()
    if not config or not config.host:
        return False, "SMTP host is not configured"
    try:
        from django.core.mail import send_mail
        send_mail(
            subject="NetPulse Test Email",
            message="This is a test email from NetPulse. Your SMTP settings are working.",
            from_email=_from_address(config), recipient_list=[to_address],
            connection=_connection(config, get_smtp_password()),
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("test email to %s failed: %s", to_address, exc)
        return False, str(exc)
