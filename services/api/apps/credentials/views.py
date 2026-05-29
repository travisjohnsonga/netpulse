from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.devices.models import Device

from .models import CredentialProfile, DeviceCredential
from .serializers import (
    CredentialProfileListSerializer,
    CredentialProfileSerializer,
    DeviceCredentialSerializer,
)
from . import probe, vault


class CredentialProfileViewSet(viewsets.ModelViewSet):
    """
    Manage reusable credential profiles (SNMP, SSH, HTTP, gNMI, NETCONF).

    Profiles store only authentication *metadata* — secret material (passwords,
    keys, community strings, tokens) is written to OpenBao and never returned on
    read. Filter by `credential_type` or `last_test_result`; search by name or
    username. Extra actions: `test/?ip=` probes reachability against an IP and
    records the result; `devices/` lists the device associations using a profile.
    """

    queryset = CredentialProfile.objects.prefetch_related("device_links").all()
    filterset_fields = ["credential_type", "last_test_result"]
    search_fields = ["name", "username", "description"]
    ordering_fields = ["name", "credential_type", "last_tested", "created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return CredentialProfileListSerializer
        return CredentialProfileSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    def perform_destroy(self, instance):
        # Remove secret material from OpenBao before dropping the metadata row.
        vault.delete_secret(instance.vault_path)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        """
        Probe reachability of this credential's service against an IP.
        Requires ``?ip=x.x.x.x``. Records the outcome on the profile.
        """
        ip = request.query_params.get("ip")
        if not ip:
            return Response(
                {"detail": "Query parameter 'ip' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = self.get_object()
        result = probe.probe(
            profile.credential_type, ip, profile.port, profile.tls_enabled
        )
        profile.last_tested = timezone.now()
        profile.last_test_result = (
            CredentialProfile.TestResult.SUCCESS if result["success"]
            else CredentialProfile.TestResult.FAILURE
        )
        profile.last_test_message = result["message"]
        profile.save(update_fields=["last_tested", "last_test_result", "last_test_message"])
        return Response({"ip": ip, **result})

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List the device associations that use this credential profile."""
        profile = self.get_object()
        links = profile.device_links.select_related("device", "credential").all()
        return Response(DeviceCredentialSerializer(links, many=True).data)


# ── Device-scoped credential association endpoints ─────────────────────────────
# Mounted under /api/devices/<device_id>/credentials/ via apps.devices.urls.


class DeviceCredentialListCreateView(generics.ListCreateAPIView):
    """GET/POST credential associations for a single device."""

    serializer_class = DeviceCredentialSerializer
    # Lets drf-spectacular introspect the model without resolving `device_id`.
    queryset = DeviceCredential.objects.none()

    def _device(self):
        return get_object_or_404(Device, pk=self.kwargs["device_id"])

    def get_queryset(self):
        return (
            DeviceCredential.objects
            .filter(device_id=self.kwargs["device_id"])
            .select_related("device", "credential")
        )

    def perform_create(self, serializer):
        device = self._device()
        purpose = serializer.validated_data.get("purpose")
        if DeviceCredential.objects.filter(device=device, purpose=purpose).exists():
            raise ValidationError(
                {"purpose": f"This device already has a credential for '{purpose}'. "
                            "Remove it first or update the existing association."}
            )
        serializer.save(device=device)


@extend_schema(
    responses={204: OpenApiResponse(description="Association removed.")},
    description="Remove the credential association for a device + purpose.",
)
class DeviceCredentialPurposeView(APIView):
    """DELETE the credential association for a device + purpose."""

    def delete(self, request, device_id, purpose):
        link = get_object_or_404(
            DeviceCredential, device_id=device_id, purpose=purpose
        )
        link.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
