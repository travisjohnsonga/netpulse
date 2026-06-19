"""
Backup API.

  GET/PUT  /api/backup/config/            — singleton config (admin to mutate)
  POST     /api/backup/run/               — run a backup now (admin)
  GET      /api/backup/records/           — history (paginated)
  GET      /api/backup/records/{id}/      — one record
  POST     /api/backup/test-connection/   — best-effort destination reachability
  GET      /api/backup/download/{id}/     — stream a local backup file (authed)

SECURITY:
  * The run endpoint REQUIRES a >=12-char password whenever a sensitive component
    (openbao / ssl certs / postgres) is included. The password is never logged,
    never stored, and never returned — only the operator-chosen hint is kept.
  * Downloads are restricted to files physically under the configured local_path
    (realpath containment) so a record path can't be coerced into traversal.
  * test-connection returns a generic message on failure (no secret/exception leak).
"""
from __future__ import annotations

import logging
import os

from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import safe_detail
from apps.core.permissions import AdminOnly

from .models import BackupConfig, BackupRecord
from .runner import run_backup
from .serializers import BackupConfigSerializer, BackupRecordSerializer

logger = logging.getLogger(__name__)

# Components whose presence makes a backup "sensitive" → password mandatory.
_SENSITIVE = ("include_openbao", "include_certs", "include_postgres")
_MIN_PASSWORD_LEN = 12


class BackupConfigView(APIView):
    """GET / PUT the singleton backup configuration."""

    def get_permissions(self):
        # Any authenticated user can read; only admins can change settings.
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [AdminOnly()]

    @extend_schema(responses=BackupConfigSerializer)
    def get(self, request):
        return Response(BackupConfigSerializer(BackupConfig.load()).data)

    @extend_schema(request=BackupConfigSerializer, responses=BackupConfigSerializer)
    def put(self, request):
        ser = BackupConfigSerializer(BackupConfig.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(BackupConfigSerializer(BackupConfig.load()).data)


class BackupRunView(APIView):
    """POST /api/backup/run/ — run a backup synchronously (admin only)."""

    permission_classes = [AdminOnly]

    @extend_schema(request=None, responses=None)
    def post(self, request):
        data = request.data or {}
        cfg = BackupConfig.load()

        include_postgres = bool(data.get("include_postgres", cfg.include_postgres))
        include_openbao = bool(data.get("include_openbao", cfg.include_openbao))
        include_config = bool(data.get("include_config", cfg.include_config_files))
        include_certs = bool(data.get("include_certs", cfg.include_ssl_certs))
        include_influxdb = bool(data.get("include_influxdb", cfg.include_influxdb))

        password = data.get("password") or ""
        password_hint = (data.get("password_hint") or "")[:256]

        sensitive = include_openbao or include_certs or include_postgres
        if sensitive:
            if not password:
                return Response(
                    {"error": "A backup that includes OpenBao secrets, SSL "
                              "certificates, or the database must be encrypted. "
                              "Provide an encryption password (minimum 12 "
                              "characters)."},
                    status=status.HTTP_400_BAD_REQUEST)
            if len(password) < _MIN_PASSWORD_LEN:
                return Response(
                    {"error": f"The encryption password must be at least "
                              f"{_MIN_PASSWORD_LEN} characters."},
                    status=status.HTTP_400_BAD_REQUEST)

        record = BackupRecord.objects.create(
            status=BackupRecord.Status.RUNNING,
            triggered_by="manual",
            components={
                "postgres": include_postgres, "openbao": include_openbao,
                "config": include_config, "certs": include_certs,
                "influxdb": include_influxdb,
            },
        )

        result = run_backup(
            include_postgres=include_postgres,
            include_openbao=include_openbao,
            include_config=include_config,
            include_certs=include_certs,
            include_influxdb=include_influxdb,
            password=password or None,
            config=cfg,
        )
        # IMPORTANT: never persist the password — only the (non-secret) hint.
        record.completed_at = timezone.now()
        record.duration_seconds = result.duration_seconds
        record.encrypted = bool(password)
        record.encryption_hint = password_hint if password else ""
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

        return Response(
            BackupRecordSerializer(record).data,
            status=status.HTTP_200_OK if result.ok else status.HTTP_502_BAD_GATEWAY,
        )


class BackupRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """Backup history (list + detail), newest first."""

    queryset = BackupRecord.objects.all()
    serializer_class = BackupRecordSerializer
    permission_classes = [IsAuthenticated]


class BackupTestConnectionView(APIView):
    """POST /api/backup/test-connection/ — best-effort destination reachability."""

    permission_classes = [AdminOnly]

    @extend_schema(request=None, responses=None)
    def post(self, request):
        from . import destinations
        cfg = BackupConfig.load()
        dest = (request.data or {}).get("destination") or cfg.destination
        try:
            ok, detail = destinations.test_destination(dest, cfg)
            return Response({"ok": ok, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            # Never leak secrets/exception internals.
            return Response(
                {"ok": False, "detail": safe_detail(
                    exc, logger, "backup test-connection",
                    public="Could not reach the configured backup destination.")})


class BackupDownloadView(APIView):
    """GET /api/backup/download/{id}/ — stream a local backup file (authed)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        record = BackupRecord.objects.filter(pk=pk).first()
        if record is None or not record.local_path:
            return Response({"error": "Backup file is no longer available."},
                            status=status.HTTP_404_NOT_FOUND)

        cfg = BackupConfig.load()
        # Path-traversal containment: the file must resolve to a location under
        # the configured local backup directory.
        try:
            base = os.path.realpath(cfg.local_path)
            abs_path = os.path.realpath(record.local_path)
        except OSError:
            return Response({"error": "Backup file is no longer available."},
                            status=status.HTTP_404_NOT_FOUND)
        if not (abs_path == base or abs_path.startswith(base + os.sep)):
            logger.warning("backup download rejected: %r not under %r", abs_path, base)
            return Response({"error": "Backup file is no longer available."},
                            status=status.HTTP_404_NOT_FOUND)
        if not os.path.isfile(abs_path):
            return Response({"error": "Backup file is no longer available."},
                            status=status.HTTP_404_NOT_FOUND)

        resp = FileResponse(open(abs_path, "rb"), content_type="application/octet-stream")
        resp["Content-Disposition"] = f'attachment; filename="{record.filename or os.path.basename(abs_path)}"'
        return resp
