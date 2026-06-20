"""Daily scheduled compliance run.

Hour-gated + same-day deduped (mirrors apps.backup.scheduler.run_due_backup /
apps.reports.tasks.run_due_schedules): the scheduler tick is short, so this
fires promptly within the configured hour and won't double-run the same day.

Default hour is 03:00 — after the 02:00 config backup, so compliance scores
against fresh configs. Override with ``COMPLIANCE_RUN_HOUR``.
"""
from __future__ import annotations

import logging
import os

from django.utils import timezone

logger = logging.getLogger(__name__)

_RUN_HOUR = int(os.environ.get("COMPLIANCE_RUN_HOUR", "3"))
_LAST_RUN_KEY = "compliance_last_scheduled_run"   # SystemSetting key → ISO date


def _is_due(now) -> bool:
    if now.hour != _RUN_HOUR:
        return False
    from apps.core.models import SystemSetting
    last = SystemSetting.get(_LAST_RUN_KEY)
    return last != now.date().isoformat()


def run_due_compliance(now=None) -> bool:
    """Run a fleet compliance pass if today's scheduled run is due.

    Returns True if a run was started this tick, False otherwise.
    """
    now = now or timezone.now()
    if not _is_due(now):
        return False

    from apps.core.models import SystemSetting

    from .runner import run_all_blocking
    # Mark the day done up-front so a long run (or a second tick within the hour)
    # can't re-trigger it; a failure won't retry until tomorrow (as with backups).
    SystemSetting.set(_LAST_RUN_KEY, now.date().isoformat())
    logger.info("scheduler: starting daily compliance run (hour=%02d:00)", _RUN_HOUR)
    ran = run_all_blocking()
    if not ran:
        logger.info("scheduler: daily compliance run skipped — another run is active")
    return ran
