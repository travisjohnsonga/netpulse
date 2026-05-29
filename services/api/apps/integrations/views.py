from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.credentials import vault

from . import netbox
from .models import NetBoxImport
from .serializers import (
    NetBoxImportRequestSerializer,
    NetBoxImportSerializer,
    NetBoxTestRequestSerializer,
    NetBoxTestResponseSerializer,
)


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
            return Response({"ok": False, "version": "", "message": str(exc)})

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
