from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.credentials import vault
from apps.credentials.models import CredentialProfile

from . import detect, fingerprint
from .models import Device, DeviceGroup, Site
from .serializers import (
    DetectPlatformRequestSerializer,
    DetectPlatformResponseSerializer,
    DeviceGroupSerializer,
    DeviceListSerializer,
    DeviceSerializer,
    SiteSerializer,
    TestConnectionRequestSerializer,
    TestConnectionResponseSerializer,
)


def _ssh_creds(profile_id):
    """Return (profile, ssh_password) for a credential profile id, or (None, None)."""
    profile = CredentialProfile.objects.filter(pk=profile_id).first()
    if not profile:
        return None, None
    secrets = vault.read_secret(profile.vault_path) if profile.vault_path else {}
    return profile, secrets.get("ssh_password", "")


class SiteViewSet(viewsets.ModelViewSet):
    """
    Manage sites/locations — a hierarchy of datacenters, campuses and branches.

    Sites carry address/geo, contact details and an optional parent for
    hierarchy. Filter by `site_type` or `parent_site`; search by name/city. The
    `devices/` action lists the devices located at a site.
    """

    queryset = Site.objects.select_related("parent_site").all()
    serializer_class = SiteSerializer
    filterset_fields = ["site_type", "parent_site"]
    search_fields = ["name", "city", "address"]
    ordering_fields = ["name", "site_type", "created_at"]

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List devices located at this site."""
        site = self.get_object()
        devices = site.devices.all()
        return Response(DeviceListSerializer(devices, many=True).data)


class DeviceGroupViewSet(viewsets.ModelViewSet):
    queryset = DeviceGroup.objects.all()
    serializer_class = DeviceGroupSerializer


class DeviceViewSet(viewsets.ModelViewSet):
    """
    Manage network devices — the core inventory of NetPulse.

    Full CRUD over devices (routers, switches, firewalls, etc.). List responses
    use a lightweight serializer; retrieve returns the full record including site,
    groups and associated credential profiles. Filter by `status`, `platform`,
    `vendor` or `site`; search across hostname, IP and serial number. The
    `topology/` action returns nodes + edges for the network map.
    """

    queryset = Device.objects.select_related("site").prefetch_related("groups").all()
    filterset_fields = ["status", "platform", "vendor", "site"]
    search_fields = ["hostname", "ip_address", "serial_number"]
    ordering_fields = ["hostname", "status", "created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return DeviceListSerializer
        return DeviceSerializer

    @extend_schema(
        request=TestConnectionRequestSerializer,
        responses=TestConnectionResponseSerializer,
        summary="Probe an IP and best-effort fingerprint a device",
    )
    @action(detail=False, methods=["post"], url_path="test-connection")
    def test_connection(self, request):
        """
        Probe management ports on an IP and infer the vendor from the SSH banner.
        Used by the Add-Device wizard's auto-detect step. Full platform/OS/model
        detection happens later in the poller (needs SNMP/credentials).
        """
        req = TestConnectionRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        ip = req.validated_data["ip"]
        result = fingerprint.fingerprint(ip)
        # If a credential profile is supplied, also run SSHDetect to fill in
        # vendor/platform/os_version (otherwise they stay null from the probe).
        profile_id = req.validated_data.get("credential_profile_id")
        if profile_id:
            profile, password = _ssh_creds(profile_id)
            if profile and profile.ssh_enabled:
                det = detect.detect_platform(ip, profile.ssh_username, password, profile.ssh_port)
                if det.get("detected"):
                    result.update({
                        "vendor": det.get("vendor") or result["vendor"],
                        "platform": det.get("platform"),
                        "os_version": det.get("os_version"),
                        "model": det.get("model"),
                    })
        return Response(TestConnectionResponseSerializer(result).data)

    @extend_schema(
        request=DetectPlatformRequestSerializer,
        responses=DetectPlatformResponseSerializer,
        summary="Auto-detect a device's platform via Netmiko SSHDetect",
    )
    @action(detail=False, methods=["post"], url_path="detect-platform")
    def detect_platform(self, request):
        """
        SSH to the device with the given credential profile, run Netmiko
        SSHDetect to identify the platform, then read show-version for OS/model/
        serial. Returns {detected, device_type, vendor, platform, …} or
        {detected: false, error}.
        """
        req = DetectPlatformRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        profile, password = _ssh_creds(req.validated_data["credential_profile_id"])
        if not profile:
            return Response({"detected": False, "error": "credential profile not found"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not profile.ssh_enabled:
            return Response({"detected": False, "error": "ssh_not_enabled"},
                            status=status.HTTP_400_BAD_REQUEST)
        result = detect.detect_platform(
            req.validated_data["ip"], profile.ssh_username, password, profile.ssh_port
        )
        return Response(DetectPlatformResponseSerializer(result).data)

    @action(detail=False, methods=["get"], url_path="topology")
    def topology(self, request):
        """
        Return nodes + edges for the network topology map.
        Topology links are populated via CDP/LLDP discovery (future).
        Returns empty edges until the topology_links table is populated.
        """
        devices = Device.objects.select_related("site").filter(
            status__in=[Device.Status.ACTIVE, Device.Status.INACTIVE, Device.Status.MAINTENANCE]
        )
        nodes = [
            {
                "id": str(d.id),
                "label": d.hostname,
                "type": d.platform,
                "site": d.site.name if d.site else None,
                "status": d.status,
                "risk_score": 0,
            }
            for d in devices
        ]
        return Response({"nodes": nodes, "edges": []})
