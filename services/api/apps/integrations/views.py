import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers as drf_serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import safe_detail
from apps.credentials import vault

from . import netbox
from .email import PROVIDER_PRESETS, send_test_email

logger = logging.getLogger(__name__)
from .models import EmailSettings, NetBoxImport, UnifiController
from .serializers import (
    EmailSettingsSerializer,
    NetBoxImportRequestSerializer,
    NetBoxImportSerializer,
    NetBoxTestRequestSerializer,
    NetBoxTestResponseSerializer,
    UnifiControllerSerializer,
)


class UnifiControllerViewSet(viewsets.ModelViewSet):
    """CRUD for UniFi controllers (one per site) + test/sync actions."""

    queryset = UnifiController.objects.all()
    serializer_class = UnifiControllerSerializer

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Verify the connection; return available sites + device count."""
        c = self.get_object()
        from apps.credentials.models import CredentialProfile

        from .unifi_client import UnifiClient, UnifiError
        from .unifi_sync import get_controller_credentials
        # Allow testing a not-yet-saved profile selection from the form, else use
        # the controller's saved credential profile.
        profile = None
        pid = (request.data or {}).get("credential_profile")
        if pid:
            profile = CredentialProfile.objects.filter(pk=pid).first()
        try:
            username, password = get_controller_credentials(c, profile=profile)
            with UnifiClient(c.host, c.port, username, password,
                             site_id=c.unifi_site_id, verify_ssl=c.verify_ssl) as client:
                devices = client.get_devices()
                sites = [s.get("name") or s.get("desc") or "" for s in client.get_sites()]
            return Response({"connected": True, "sites": [s for s in sites if s],
                             "device_count": len(devices)})
        except UnifiError as exc:
            return Response(
                {"connected": False, "error": safe_detail(
                    exc, logger, "unifi test-connection",
                    public="Could not connect to the UniFi controller.")},
                status=status.HTTP_502_BAD_GATEWAY)

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        """Import this controller's managed devices into inventory."""
        c = self.get_object()
        from .unifi_client import UnifiError
        from .unifi_sync import sync_controller
        try:
            return Response(sync_controller(c))
        except UnifiError as exc:
            return Response({"error": safe_detail(exc, logger, "unifi sync",
                                                  public="UniFi sync failed.")},
                            status=status.HTTP_502_BAD_GATEWAY)

    @extend_schema(request=None, responses=None)
    @action(detail=False, methods=["post"], url_path="sync-all")
    def sync_all(self, request):
        """Sync every enabled controller (best-effort)."""
        from .unifi_sync import sync_all_controllers
        return Response(sync_all_controllers())

    # ── UniFi Site Manager (cloud) account ────────────────────────────────────
    @extend_schema(request=None, responses=None)
    @action(detail=False, methods=["get", "put"], url_path="cloud")
    def cloud(self, request):
        """GET / PUT the singleton UniFi cloud (Site Manager) account."""
        from .models import UnifiCloudAccount
        from .serializers import UnifiCloudAccountSerializer
        account = UnifiCloudAccount.load()
        if request.method == "GET":
            return Response(UnifiCloudAccountSerializer(account).data)
        ser = UnifiCloudAccountSerializer(account, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(UnifiCloudAccountSerializer(UnifiCloudAccount.load()).data)

    @extend_schema(request=None, responses=None)
    @action(detail=False, methods=["post"], url_path="cloud/test")
    def cloud_test(self, request):
        """Verify the cloud API key works; return the host count."""
        from .unifi_cloud import UnifiCloudClient, UnifiCloudError, _read_api_key
        api_key = (request.data or {}).get("api_key") or _read_api_key()
        if not api_key:
            return Response({"connected": False, "error": "No API key configured"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            hosts = UnifiCloudClient(api_key).get_hosts()
            return Response({"connected": True, "host_count": len(hosts)})
        except UnifiCloudError as exc:
            return Response(
                {"connected": False, "error": safe_detail(
                    exc, logger, "unifi cloud test",
                    public="Could not connect to UniFi Site Manager.")},
                status=status.HTTP_502_BAD_GATEWAY)

    @extend_schema(request=None, responses=None)
    @action(detail=False, methods=["post"], url_path="cloud/discover")
    def cloud_discover(self, request):
        """Auto-discover all controllers from the cloud account (upsert)."""
        from .unifi_cloud import discover_controllers, UnifiCloudError
        try:
            return Response(discover_controllers())
        except UnifiCloudError as exc:
            return Response({"error": safe_detail(exc, logger, "unifi cloud discover",
                                                  public="UniFi discovery failed.")},
                            status=status.HTTP_502_BAD_GATEWAY)


class EmailSettingsView(APIView):
    """GET / PUT the singleton SMTP configuration (Settings → Integrations → Email)."""

    @extend_schema(responses=EmailSettingsSerializer)
    def get(self, request):
        data = EmailSettingsSerializer(EmailSettings.load()).data
        data["provider_presets"] = PROVIDER_PRESETS
        return Response(data)

    @extend_schema(request=EmailSettingsSerializer, responses=EmailSettingsSerializer)
    def put(self, request):
        ser = EmailSettingsSerializer(EmailSettings.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(EmailSettingsSerializer(EmailSettings.load()).data)


class EmailTestView(APIView):
    """Send a test email with the saved settings to verify the configuration."""

    @extend_schema(
        request=drf_serializers.Serializer,
        responses=drf_serializers.Serializer,
    )
    def post(self, request):
        to = (request.data or {}).get("to", "")
        if not to:
            return Response({"error": "A recipient address ('to') is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        # send_test_email logs the underlying SMTP error server-side; never echo
        # the raw exception text back to the client (information exposure).
        ok, _err = send_test_email(to)
        if ok:
            return Response({"sent": True})
        return Response(
            {"sent": False,
             "error": "Failed to send the test email. Verify the SMTP host, port, "
                      "and credentials, then check the server logs for details."},
            status=status.HTTP_502_BAD_GATEWAY)


class NetBoxImportViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Import inventory from NetBox (v3.x / v4.x).

    `GET` lists past imports (history). `POST` runs a new import: sites first,
    then devices, skipping anything already present. The NetBox API token is
    written to OpenBao — only its path is stored. `test-connection/` verifies
    reachability and reports the detected NetBox version.
    """

    queryset = NetBoxImport.objects.all()
    serializer_class = NetBoxImportSerializer
    ordering = ["-created_at"]

    @extend_schema(request=NetBoxTestRequestSerializer, responses=NetBoxTestResponseSerializer)
    @action(detail=False, methods=["post"], url_path="test-connection")
    def test_connection(self, request):
        req = NetBoxTestRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        client = netbox.NetBoxClient(
            req.validated_data["netbox_url"], req.validated_data["api_credential"],
            verify_ssl=req.validated_data["verify_ssl"])
        try:
            version = client.detect_version()
            return Response({"ok": True, "version": version, "message": f"Connected to NetBox {version}."})
        except netbox.NetBoxError as exc:
            return Response({"ok": False, "version": "", "message": safe_detail(
                exc, logger, "netbox test-connection",
                public="Could not connect to NetBox. Check the URL and API token.")})

    @extend_schema(request=NetBoxTestRequestSerializer, responses=None)
    @action(detail=False, methods=["post"])
    def preview(self, request):
        """Dry-run an import — show create/update/skip + credential assignments, no writes."""
        req = NetBoxImportRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        client = netbox.NetBoxClient(
            req.validated_data["netbox_url"], req.validated_data["api_credential"],
            verify_ssl=req.validated_data["verify_ssl"])
        try:
            client.detect_version()
            return Response(netbox.preview_import(client, req.validated_data.get("import_options") or {}))
        except netbox.NetBoxError as exc:
            return Response({"error": safe_detail(exc, logger, "netbox preview",
                            public="Could not connect to NetBox. Check the URL and API token.")},
                            status=status.HTTP_502_BAD_GATEWAY)

    @extend_schema(request=NetBoxImportRequestSerializer, responses=NetBoxImportSerializer)
    def create(self, request):
        req = NetBoxImportRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        url = req.validated_data["netbox_url"]
        token = req.validated_data["api_credential"]
        options = req.validated_data.get("import_options") or {}
        verify_ssl = req.validated_data["verify_ssl"]

        record = NetBoxImport.objects.create(
            netbox_url=url,
            options=options,
            verify_ssl=verify_ssl,
            status=NetBoxImport.Status.RUNNING,
            started_at=timezone.now(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        # Persist the combined v2 credential to OpenBao (path only in the DB).
        record.vault_path = f"netpulse/integrations/netbox/{record.pk}"
        record.save(update_fields=["vault_path"])
        vault.write_secret(record.vault_path, {"api_key": token})

        client = netbox.NetBoxClient(url, token, verify_ssl=verify_ssl)
        try:
            record.netbox_version = client.detect_version()
            summary = netbox.run_import(client, options)
            record.sites_imported = summary["sites_imported"]
            record.devices_imported = summary["devices_imported"]
            record.devices_updated = summary["devices_updated"]
            record.skipped = summary["skipped"]
            record.errors = summary["errors"]
            record.status = NetBoxImport.Status.COMPLETED
        except netbox.NetBoxError as exc:
            record.status = NetBoxImport.Status.FAILED
            record.errors = [safe_detail(exc, logger, "netbox import",
                                         public="NetBox import failed.")]
        record.finished_at = timezone.now()
        record.save()

        code = status.HTTP_201_CREATED if record.status == NetBoxImport.Status.COMPLETED else status.HTTP_502_BAD_GATEWAY
        return Response(NetBoxImportSerializer(record).data, status=code)
