"""
Report artifact storage + email delivery.

Reports are written under ``MEDIA_ROOT/reports/{year}/{month}/`` and recorded as
GeneratedReport rows; the file is served only through the authenticated
``/api/reports/{id}/download/`` endpoint.
"""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.utils import timezone

from .models import GeneratedReport

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "csv": "text/csv",
    "json": "application/json",
    "html": "text/html",
}


def content_type(fmt: str) -> str:
    return CONTENT_TYPES.get(fmt, "application/octet-stream")


def _as_bytes(content) -> bytes:
    return content if isinstance(content, bytes) else str(content).encode("utf-8")


def store_report(*, report_type: str, fmt: str, content, params: dict,
                 user=None, source: str = "on-demand") -> GeneratedReport:
    """Persist a generated report to disk + DB. Returns the GeneratedReport row."""
    data = _as_bytes(content)
    now = timezone.now()
    rel_dir = os.path.join("reports", f"{now:%Y}", f"{now:%m}")
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    fname = f"{report_type}_{now:%Y%m%d_%H%M%S}.{fmt}"
    rel_path = os.path.join(rel_dir, fname)
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    with open(abs_path, "wb") as fh:
        fh.write(data)
    return GeneratedReport.objects.create(
        report_type=report_type, generated_by=(user if getattr(user, "is_authenticated", False) else None),
        source=source, parameters=params, file_path=rel_path, file_size=len(data), format=fmt)


def download_filename(report: GeneratedReport) -> str:
    # Operations reports get a period-specific prefix (daily/weekly/monthly/
    # quarterly-ops); other report types keep the report_type prefix.
    from .models import ReportType
    prefix = report.report_type
    if report.report_type == ReportType.DAILY_OPS:
        period = (report.parameters or {}).get("period") or "daily"
        prefix = {"daily": "daily-ops", "weekly": "weekly-ops",
                  "monthly": "monthly-ops", "quarterly": "quarterly-ops"}.get(period, "daily-ops")
    return f"spane-{prefix}-{report.generated_at:%Y%m%d}.{report.format}"


def email_report(recipients, subject: str, body: str,
                 attachment: bytes, filename: str, fmt: str) -> bool:
    """Email a report with the artifact attached. Best-effort (never raises)."""
    recipients = [r for r in (recipients or []) if r]
    if not recipients:
        return False
    try:
        from apps.integrations.email import configured_connection
        connection, from_email = configured_connection()
        if connection is None:
            logger.info("report email skipped: SMTP not configured/enabled")
            return False
        from django.core.mail import EmailMessage
        msg = EmailMessage(subject=subject, body=body, from_email=from_email,
                           to=recipients, connection=connection)
        msg.attach(filename, attachment, content_type(fmt))
        msg.send(fail_silently=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("report email failed: %s", exc)
        return False
