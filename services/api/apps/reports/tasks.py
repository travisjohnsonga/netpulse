"""Scheduled report delivery — invoked by the run_scheduler loop each tick."""
from __future__ import annotations

import logging

from django.utils import timezone

from .generate import generate
from .models import ReportSchedule, ReportType
from .storage import download_filename, email_report

logger = logging.getLogger(__name__)


def email_content(report_type: str, data: dict, when) -> tuple[str, str]:
    """(subject, plain-text body) for a report's delivery email."""
    date_h = when.strftime("%b %d, %Y")
    if report_type == ReportType.DAILY_OPS:
        sec = data.get("security_events", {})
        av = data.get("device_availability", {})
        ce = data.get("compliance_events", {})
        ch = data.get("collection_health", {})
        cc = data.get("config_changes", [])
        site = "All Sites"
        lines = [
            f"spane Daily Operations Report — {data.get('report_date', date_h)} — {site}",
            "", "Quick Summary:",
            f"- Security: {sec.get('total_failures', 0)} login failures, "
            f"{len(sec.get('after_hours_logins', []))} after-hours",
            f"- Availability: {av.get('total_outages', 0)} outage(s) "
            f"({av.get('total_downtime_minutes', 0)} min, {av.get('availability_pct', 100)}%)",
            f"- Config Changes: {len(cc)} device(s) changed",
        ]
        for c in cc[:8]:
            lines.append(f"  - {c['hostname']}: {c.get('diff_summary') or 'changed'} "
                         f"(+{c['lines_added']}/-{c['lines_removed']})")
        rate = None
        if ch.get("total_attempts"):
            rate = round(ch["successful"] / ch["total_attempts"] * 100, 1)
        lines += [
            f"- Compliance: {ce.get('total_failing_devices', 0)} device(s) below threshold",
            f"- Collection: {ch.get('successful', 0)}/{ch.get('total_attempts', 0)} successful"
            + (f" ({rate}%)" if rate is not None else ""),
            "", "Full report attached.", "", "Powered by spane",
        ]
        return f"spane Daily Ops Report - {date_h}", "\n".join(lines)

    # Compliance Summary
    s = data.get("summary", {})
    body = (
        f"spane Compliance Summary — {date_h}\n\n"
        f"- Devices: {s.get('total_devices', 0)} "
        f"(passing {s.get('passing', 0)}, warning {s.get('warning', 0)}, failing {s.get('failing', 0)})\n"
        f"- Average score: {s.get('avg_score', '—')}\n"
        f"- Startup-config mismatches: {len(data.get('startup_mismatch', []))}\n\n"
        "Full report attached.\n\nPowered by spane")
    return f"spane Compliance Summary - {date_h}", body


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
            report, content, data = generate(
                schedule.report_type, schedule.fmt, schedule.parameters or {},
                user=None, source="scheduled")
            subject, body = email_content(schedule.report_type, data, now)
            sent = email_report(
                schedule.recipients, subject=subject, body=body,
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
