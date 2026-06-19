"""
Scheduled backups — invoked by the run_scheduler loop each tick.

Hour-gated + same-day deduped (mirrors apps.reports.tasks.run_due_schedules):
the scheduler tick is short, so this fires promptly within the configured hour
and won't double-run the same day.

SECURITY: scheduled backups use the encryption password stored in OpenBao at
spane/backup/encryption (key "password"). If ``encryption_required`` is set and
no password is stored, the backup is SKIPPED with a logged warning — a sensitive
backup is never written unencrypted.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from apps.credentials import vault

from .models import ENCRYPTION_VAULT_PATH, BackupConfig, BackupRecord
from .runner import run_backup

logger = logging.getLogger(__name__)


def _is_due(cfg: BackupConfig, now) -> bool:
    if cfg.schedule == BackupConfig.Schedule.DISABLED:
        return False
    if now.hour != cfg.schedule_time.hour:
        return False
    if cfg.schedule == BackupConfig.Schedule.WEEKLY:
        if cfg.schedule_day is None or now.weekday() != cfg.schedule_day:
            return False
    if cfg.schedule == BackupConfig.Schedule.MONTHLY:
        if cfg.schedule_day is None or now.day != cfg.schedule_day:
            return False
    # Same-day dedup: don't fire if a scheduled backup already ran today.
    last = BackupRecord.objects.filter(triggered_by="scheduled").order_by("-started_at").first()
    if last and last.started_at.date() == now.date():
        return False
    return True


def run_due_backup(now=None) -> bool:
    """Run a scheduled backup if one is due. Returns True if a backup was run."""
    now = now or timezone.now()
    cfg = BackupConfig.load()
    if not _is_due(cfg, now):
        return False

    sensitive = cfg.include_openbao or cfg.include_ssl_certs or cfg.include_postgres
    password = (vault.read_secret(ENCRYPTION_VAULT_PATH) or {}).get("password") or ""

    if sensitive and not password and cfg.encryption_required:
        logger.warning(
            "scheduled backup skipped: encryption required and includes sensitive "
            "data (openbao/certs/postgres) but no encryption password is stored at "
            "%s. Set one in Settings → Backup.", ENCRYPTION_VAULT_PATH)
        return False

    record = BackupRecord.objects.create(
        status=BackupRecord.Status.RUNNING, triggered_by="scheduled",
        components={
            "postgres": cfg.include_postgres, "openbao": cfg.include_openbao,
            "config": cfg.include_config_files, "certs": cfg.include_ssl_certs,
            "influxdb": cfg.include_influxdb,
        },
    )
    result = run_backup(
        include_postgres=cfg.include_postgres,
        include_openbao=cfg.include_openbao,
        include_config=cfg.include_config_files,
        include_certs=cfg.include_ssl_certs,
        include_influxdb=cfg.include_influxdb,
        password=password or None,
        config=cfg,
    )
    record.completed_at = timezone.now()
    record.duration_seconds = result.duration_seconds
    record.encrypted = bool(password)
    record.components = result.components or record.components
    if result.ok:
        record.status = BackupRecord.Status.SUCCESS
        record.filename = result.filename
        record.local_path = result.archive_path
        record.file_size_bytes = result.size_bytes
    else:
        record.status = BackupRecord.Status.FAILED
        record.error_message = result.error
    record.save()
    logger.info("scheduled backup finished: %s (%s)", record.status, record.filename)
    return True
