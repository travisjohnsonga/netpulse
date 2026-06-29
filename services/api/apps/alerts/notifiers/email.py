"""Email notifier — renders a subject + text/HTML body and sends via the
DB-configured SMTP (Settings → Integrations → Email), falling back to the
env-configured Django backend. Recipients come from ``config.recipients``."""
from __future__ import annotations

import logging

from . import Notifier, register

logger = logging.getLogger(__name__)


def _recipients(channel) -> list[str]:
    cfg = channel.config or {}
    recips = cfg.get("recipients") or cfg.get("to") or []
    if isinstance(recips, str):
        recips = [r.strip() for r in recips.replace(";", ",").split(",")]
    return [r for r in recips if r]


def _render(payload) -> tuple[str, str, str]:
    """Return (subject, text_body, html_body)."""
    subject = payload.subject()

    rows = []
    if payload.device:
        rows.append(("Device", payload.device))
    rows.append(("Severity", payload.severity.upper()))
    rows.append(("State", payload.state_word))
    if payload.rule_name:
        rows.append(("Rule", payload.rule_name))
    if payload.is_resolved:
        if payload.resolved_at:
            rows.append(("Resolved at", payload.resolved_at))
        if payload.resolved_by:
            rows.append(("Resolved by", payload.resolved_by))
        if payload.resolution_note:
            rows.append(("Note", payload.resolution_note))
    elif payload.fired_at:
        rows.append(("Fired at", payload.fired_at))

    text_lines = [subject, ""]
    if payload.message:
        text_lines += [payload.message, ""]
    text_lines += [f"{k}: {v}" for k, v in rows]
    if payload.link:
        text_lines += ["", f"View in spane: {payload.link}"]
    text_body = "\n".join(text_lines)

    bar = f"#{payload.color}"
    fact_rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#666;white-space:nowrap;">{k}</td>'
        f'<td style="padding:4px 0;color:#111;">{_esc(v)}</td></tr>'
        for k, v in rows
    )
    btn = ""
    if payload.link:
        btn = (f'<p style="margin:18px 0 0;"><a href="{_esc(payload.link)}" '
               f'style="background:{bar};color:#fff;text-decoration:none;'
               f'padding:9px 16px;border-radius:4px;font-size:14px;">View in spane</a></p>')
    html_body = (
        f'<div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;max-width:560px;">'
        f'<div style="border-left:4px solid {bar};padding:8px 14px;background:#fafafa;">'
        f'<h2 style="margin:0;font-size:17px;color:#111;">{payload.emoji} {_esc(payload.title)}</h2>'
        f'<div style="font-size:13px;color:#666;margin-top:2px;">{payload.state_word} · '
        f'{payload.severity.upper()}</div></div>'
        + (f'<p style="font-size:14px;color:#222;">{_esc(payload.message)}</p>' if payload.message else "")
        + f'<table style="font-size:13px;border-collapse:collapse;">{fact_rows}</table>'
        + btn
        + '<p style="font-size:11px;color:#999;margin-top:22px;">spane — unified infrastructure visibility</p>'
        + '</div>'
    )
    return subject, text_body, html_body


def _esc(value) -> str:
    from django.utils.html import escape
    return escape(str(value))


@register("email")
class EmailNotifier(Notifier):
    def send(self, channel, payload) -> tuple[bool, str]:
        recipients = _recipients(channel)
        if not recipients:
            return False, "no recipients configured"
        subject, text_body, html_body = _render(payload)
        try:
            from apps.integrations.email import configured_connection
            from django.conf import settings
            from django.core.mail import EmailMultiAlternatives

            conn, from_email = configured_connection()
            if from_email is None:
                from_email = (getattr(settings, "EMAIL_FROM", None)
                              or getattr(settings, "DEFAULT_FROM_EMAIL", "netpulse@localhost"))
            # Per-channel from / reply-to overrides.
            cfg = channel.config or {}
            from_email = cfg.get("from") or from_email
            reply_to = cfg.get("reply_to")
            headers = {}
            msg = EmailMultiAlternatives(
                subject=subject, body=text_body, from_email=from_email,
                to=recipients, connection=conn,
                reply_to=[reply_to] if reply_to else None, headers=headers,
            )
            msg.attach_alternative(html_body, "text/html")
            msg.send(fail_silently=False)
            return True, f"sent to {len(recipients)} recipient(s)"
        except Exception as exc:  # noqa: BLE001
            logger.warning("email notify failed for channel %s: %s", channel.pk, exc)
            return False, str(exc)
