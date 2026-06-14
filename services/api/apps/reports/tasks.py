"""Scheduled report delivery — invoked by the run_scheduler loop each tick."""
from __future__ import annotations

import logging

from django.utils import timezone

from .generate import generate
from .models import ReportSchedule
from .storage import download_filename, email_report

logger = logging.getLogger(__name__)


def _is_due(schedule: ReportSchedule, now) -> bool:
    """True when the schedule should fire now and hasn't already run today/this period."""
    if now.hour != schedule.hour:
        return False
    if schedule.frequency == ReportSchedule.Frequency.WEEKLY and now.weekday() != schedule.day_of_week:
        return False
    if schedule.frequency == ReportSchedule.Frequency.MONTHLY and now.day != schedule.day_of_month:
        return False
    # Don't double-fire within the same calendar day.
    if schedule.last_run and schedule.last_run.date() == now.date():
        return False
    return True


def run_due_schedules(now=None) -> int:
    """Generate + email every due schedule. Returns the number fired."""
    now = now or timezone.now()
    fired = 0
    for schedule in ReportSchedule.objects.filter(enabled=True):
        if not _is_due(schedule, now):
            continue
        try:
            report, content = generate(
                schedule.report_type, schedule.fmt, schedule.parameters or {},
                user=None, source="scheduled")
            sent = email_report(
                schedule.recipients,
                subject=f"spane {report.title} — {now:%Y-%m-%d}",
                body=f"Attached: {report.title} generated {now:%Y-%m-%d %H:%M} UTC by spane.",
                attachment=content, filename=download_filename(report), fmt=schedule.fmt)
            schedule.last_run = now
            schedule.last_status = "sent" if sent else "generated (email not sent — SMTP?)"
            schedule.save(update_fields=["last_run", "last_status", "updated_at"])
            fired += 1
            logger.info("scheduled report %s fired (emailed=%s)", schedule.report_type, sent)
        except Exception as exc:  # noqa: BLE001 — one bad schedule must not stop the rest
            logger.error("scheduled report %s failed: %s", schedule.report_type, exc)
            schedule.last_run = now
            schedule.last_status = f"error: {exc}"[:255]
            schedule.save(update_fields=["last_run", "last_status", "updated_at"])
    return fired
