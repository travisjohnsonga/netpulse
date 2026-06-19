"""
Backup execution — the single choke point that shells out to scripts/backup.sh.

Kept separate from the views so tests can monkeypatch ``run_backup`` to a fake
success without touching real infra, and so the scheduler and the API share one
implementation.

SECURITY: the encryption password is passed to the script via the ``BACKUP_PASSWORD``
environment variable — NEVER as a command-line argument (argv is visible in
``ps``). It is never logged and never returned. ``openssl enc -pass env:VAR``
reads it from the same env so it never appears in the child's argv either.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from apps.credentials import vault

from .models import (
    GIT_VAULT_PATH,
    S3_VAULT_PATH,
    SCP_VAULT_PATH,
    BackupConfig,
)

logger = logging.getLogger(__name__)

# Repo root holds scripts/backup.sh + docker-compose.yml. The api container runs
# under /app (services/api); the repo is bind-mounted at the configured path. Use
# BACKUP_REPO_DIR if set, else default to the conventional on-host location.
REPO_DIR = os.environ.get("BACKUP_REPO_DIR", "/opt/spane")


def _script(name: str) -> str:
    return str(Path(REPO_DIR) / "scripts" / name)


@dataclass
class BackupResult:
    ok: bool
    archive_path: str = ""
    filename: str = ""
    size_bytes: int | None = None
    duration_seconds: int = 0
    error: str = ""
    stdout: str = ""
    components: dict = field(default_factory=dict)


def _bool_env(value: bool) -> str:
    return "true" if value else "false"


def run_backup(
    *,
    include_postgres: bool,
    include_openbao: bool,
    include_config: bool,
    include_certs: bool,
    include_influxdb: bool = False,
    password: str | None = None,
    config: BackupConfig | None = None,
) -> BackupResult:
    """
    Invoke ``scripts/backup.sh`` with the requested components and (optional)
    encryption password. Runs synchronously — a real backup can be slow.

    The password (when given) is exported as ``BACKUP_PASSWORD`` in the child
    environment ONLY; it is never placed on the command line and never logged.
    """
    cfg = config or BackupConfig.load()
    components = {
        "postgres": include_postgres,
        "openbao": include_openbao,
        "config": include_config,
        "certs": include_certs,
        "influxdb": include_influxdb,
    }

    env = dict(os.environ)
    env.update({
        "BACKUP_POSTGRES": _bool_env(include_postgres),
        "BACKUP_OPENBAO": _bool_env(include_openbao),
        "BACKUP_CONFIG": _bool_env(include_config),
        "BACKUP_CERTS": _bool_env(include_certs),
        "BACKUP_INFLUXDB": _bool_env(include_influxdb),
        "BACKUP_INFLUXDB_DAYS": str(cfg.include_influxdb_days),
        "BACKUP_LOCAL_PATH": cfg.local_path,
        "BACKUP_DEST": cfg.destination,
        "BACKUP_RETENTION_DAYS": str(cfg.retention_days),
        # Non-secret destination context (secrets come from OpenBao below).
        "SCP_HOST": cfg.scp_host, "SCP_PORT": str(cfg.scp_port),
        "SCP_USERNAME": cfg.scp_username, "SCP_PATH": cfg.scp_path,
        "GIT_REPO_URL": cfg.git_repo_url, "GIT_BRANCH": cfg.git_branch,
        "GIT_PATH": cfg.git_path,
        "S3_BUCKET": cfg.s3_bucket, "S3_PREFIX": cfg.s3_prefix,
        "S3_ENDPOINT": cfg.s3_endpoint, "S3_REGION": cfg.s3_region,
    })

    # Destination secrets from OpenBao (never the DB). Best-effort: absent
    # secrets just mean the upload step will no-op/fail in the script.
    if cfg.destination == BackupConfig.Destination.SCP:
        scp = vault.read_secret(SCP_VAULT_PATH) or {}
        if scp.get("password"):
            env["SCP_PASSWORD"] = scp["password"]
        if scp.get("ssh_key"):
            env["SCP_SSH_KEY"] = scp["ssh_key"]
    elif cfg.destination == BackupConfig.Destination.GIT:
        git = vault.read_secret(GIT_VAULT_PATH) or {}
        if git.get("ssh_key"):
            env["GIT_SSH_KEY"] = git["ssh_key"]
    elif cfg.destination == BackupConfig.Destination.S3:
        s3 = vault.read_secret(S3_VAULT_PATH) or {}
        if s3.get("access_key"):
            env["S3_ACCESS_KEY"] = s3["access_key"]
        if s3.get("secret_key"):
            env["S3_SECRET_KEY"] = s3["secret_key"]

    # Encryption password via ENV only — never argv, never logged.
    if password:
        env["BACKUP_PASSWORD"] = password
        env["INCLUDE_SECRETS"] = "true"
    else:
        env.pop("BACKUP_PASSWORD", None)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["bash", _script("backup.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("BACKUP_TIMEOUT_S", "3600")),
        )
    except subprocess.TimeoutExpired:
        return BackupResult(ok=False, error="Backup timed out.", components=components,
                            duration_seconds=int(time.monotonic() - started))
    except Exception as exc:  # noqa: BLE001
        logger.error("backup subprocess failed to launch: %s", exc, exc_info=True)
        return BackupResult(ok=False, error="Backup could not be started.",
                            components=components,
                            duration_seconds=int(time.monotonic() - started))

    duration = int(time.monotonic() - started)
    if proc.returncode != 0:
        # Log full stderr server-side; surface a generic, non-secret message.
        logger.error("backup.sh exited %s: %s", proc.returncode, proc.stderr)
        # The script's last stderr line is a safe operator-facing reason.
        reason = (proc.stderr or "").strip().splitlines()
        detail = reason[-1] if reason else "Backup failed."
        return BackupResult(ok=False, error=detail[:500], components=components,
                            duration_seconds=duration, stdout=proc.stdout)

    archive_path = (proc.stdout or "").strip().splitlines()
    archive = archive_path[-1].strip() if archive_path else ""
    size = None
    try:
        if archive and os.path.exists(archive):
            size = os.path.getsize(archive)
    except OSError:
        size = None
    return BackupResult(
        ok=True, archive_path=archive, filename=os.path.basename(archive),
        size_bytes=size, duration_seconds=duration, components=components,
        stdout=proc.stdout,
    )
