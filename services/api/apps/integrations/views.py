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
from .models import EmailSettings, NetBoxImport
from .serializers import (
    EmailSettingsSerializer,
    NetBoxImportRequestSerializer,
    NetBoxImportSerializer,
    NetBoxTestRequestSerializer,
    NetBoxTestResponseSerializer,
)


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
        ok, err = send_test_email(to)
        if ok:
            return Response({"sent": True})
        return Response({"sent": False, "error": err or "Failed to send test email."},
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
        client = netbox.NetBoxClient(req.validated_data["netbox_url"], req.validated_data["api_token"])
        try:
            version = client.detect_version()
            return Response({"ok": True, "version": version, "message": f"Connected to NetBox {version}."})
        except netbox.NetBoxError as exc:
            return Response({"ok": False, "version": "", "message": safe_detail(
                exc, logger, "netbox test-connection",
                public="Could not connect to NetBox. Check the URL and API token.")})

    @extend_schema(request=NetBoxImportRequestSerializer, responses=NetBoxImportSerializer)
    def create(self, request):
        req = NetBoxImportRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        url = req.validated_data["netbox_url"]
        token = req.validated_data["api_token"]
        options = req.validated_data.get("import_options") or {}

        record = NetBoxImport.objects.create(
            netbox_url=url,
            options=options,
            status=NetBoxImport.Status.RUNNING,
            started_at=timezone.now(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        # Persist the token to OpenBao (path only in the DB).
        record.vault_path = f"netpulse/integrations/netbox/{record.pk}"
        record.save(update_fields=["vault_path"])
        vault.write_secret(record.vault_path, {"api_token": token})

        client = netbox.NetBoxClient(url, token)
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
            record.errors = [str(exc)]
        record.finished_at = timezone.now()
        record.save()

        code = status.HTTP_201_CREATED if record.status == NetBoxImport.Status.COMPLETED else status.HTTP_502_BAD_GATEWAY
        return Response(NetBoxImportSerializer(record).data, status=code)
