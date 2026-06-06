import logging
import socket
import urllib.parse

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.credentials import vault

from .diff import generate_diff
from .models import ConfigBackupSettings, DeviceConfig

logger = logging.getLogger(__name__)
from .serializers import (
    ConfigBackupSettingsSerializer,
    ConfigDiffRequestSerializer,
    ConfigDiffResponseSerializer,
    DeviceConfigSerializer,
    SimpleResultSerializer,
    TestGitRequestSerializer,
)


class DeviceConfigViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Browse stored device configuration snapshots.

    Read-only list/retrieve, newest first; filter with ?device=<id>. The
    `collect/<device_id>/` action triggers an immediate collection for a device.
    """

    serializer_class = DeviceConfigSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = DeviceConfig.objects.all().order_by("-collected_at")
        device_id = self.request.query_params.get("device")
        if device_id:
            qs = qs.filter(device_id=device_id)
        return qs

    @extend_schema(request=None, responses=SimpleResultSerializer)
    @action(detail=False, methods=["post"], url_path="collect/(?P<device_id>[^/.]+)")
    def collect(self, request, device_id=None):
        """Trigger an immediate (manual) config collection for one device."""
        from django.core.management import call_command
        call_command("run_config_manager", device_id=device_id, once=True)
        return Response({"status": "collection triggered"})

    @extend_schema(request=ConfigDiffRequestSerializer, responses=ConfigDiffResponseSerializer)
    @action(detail=False, methods=["post"], url_path="diff")
    def diff(self, request):
        """
        Structured unified diff between two configs.

        Supply either two snapshot ids (`left`/`right`) or two raw strings
        (`old`/`new`). Returns summary counts plus hunks of context/add/remove
        lines with line numbers, for the Configuration Compare diff viewer.
        """
        req = ConfigDiffRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        data = req.validated_data

        def _content(config_id):
            cfg = DeviceConfig.objects.filter(pk=config_id).first()
            if cfg is None:
                return None
            return cfg.content or ""

        if data.get("left") is not None or data.get("right") is not None:
            old = _content(data.get("left"))
            new = _content(data.get("right"))
            missing = [
                str(cid) for cid, val in
                ((data.get("left"), old), (data.get("right"), new))
                if cid is not None and val is None
            ]
            if missing:
                return Response(
                    {"error": f"Config snapshot(s) not found: {', '.join(missing)}."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            old, new = old or "", new or ""
        else:
            old = data.get("old", "")
            new = data.get("new", "")

        return Response(generate_diff(old, new, context=data.get("context", 3)))


class ConfigBackupSettingsView(generics.RetrieveUpdateAPIView):
    """Get or update the (singleton) configuration-backup settings."""

    serializer_class = ConfigBackupSettingsSerializer

    def get_object(self):
        return ConfigBackupSettings.load()

    def perform_update(self, serializer):
        settings_obj = serializer.instance
        credential = serializer.validated_data.pop("git_credential", None)
        obj = serializer.save()
        if credential:
            if not obj.git_vault_path:
                obj.git_vault_path = "netpulse/configbackup/git"
                obj.save(update_fields=["git_vault_path"])
            vault.write_secret(obj.git_vault_path, {"git_credential": credential})
        _ = settings_obj


def _probe_host(repo_url: str, ssh: bool, timeout: float = 3.0) -> tuple[bool, str]:
    """Best-effort reachability check of the git host (TCP)."""
    if not repo_url:
        return False, "No repository URL configured."
    host = None
    port = 22 if ssh else 443
    if repo_url.startswith(("http://", "https://")):
        host = urllib.parse.urlparse(repo_url).hostname
        port = 80 if repo_url.startswith("http://") else 443
    elif "@" in repo_url and ":" in repo_url:  # git@host:org/repo.git
        host = repo_url.split("@", 1)[1].split(":", 1)[0]
        port = 22
    else:
        host = urllib.parse.urlparse("ssh://" + repo_url).hostname
    if not host:
        return False, f"Could not parse a host from {repo_url!r}."
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"Reachable: {host}:{port}. Full auth is verified by the config-manager worker."
    except OSError as exc:
        # Log the OS-level detail; return only host:port (no raw exception text).
        logger.info("git host probe failed for %s:%s: %s", host, port, exc)
        return False, f"{host}:{port} is unreachable."


class TestGitView(APIView):
    """Probe reachability of the configured (or supplied) git repository host."""

    @extend_schema(request=TestGitRequestSerializer, responses=SimpleResultSerializer)
    def post(self, request):
        obj = ConfigBackupSettings.load()
        repo = request.data.get("git_repo_url") or obj.git_repo_url
        ssh = obj.git_auth_method in ("ssh_key", "deploy_key") or repo.startswith("git@")
        ok, message = _probe_host(repo, ssh)
        return Response({"ok": ok, "message": message})


class SyncNowView(APIView):
    """
    Request an immediate git sync.

    The actual commit/push is performed by the config-manager worker; this records
    the request and surfaces config status honestly.
    """

    @extend_schema(request=None, responses=SimpleResultSerializer)
    def post(self, request):
        obj = ConfigBackupSettings.load()
        if not obj.git_enabled:
            return Response({"ok": False, "message": "Git sync is disabled. Enable it and save first."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not obj.git_repo_url:
            return Response({"ok": False, "message": "No repository URL configured."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Record the request; the config-manager worker performs the push.
        obj.last_sync_at = timezone.now()
        obj.last_sync_success = None  # outcome set by the worker
        obj.save(update_fields=["last_sync_at", "last_sync_success"])
        return Response({
            "ok": True,
            "message": "Sync requested. The config-manager worker will push pending configs to the repository.",
            "last_commit_sha": obj.last_commit_sha,
        })
