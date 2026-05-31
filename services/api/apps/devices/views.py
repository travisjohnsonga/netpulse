from drf_spectacular.utils import OpenApiParameter, extend_schema
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
    ordering_fields = [
        "hostname", "status", "ip_address", "vendor", "platform", "model",
        "os_version", "serial_number", "last_seen", "created_at", "site__name",
    ]
    ordering = ["hostname"]

    def get_serializer_class(self):
        if self.action == "list":
            return DeviceListSerializer
        return DeviceSerializer

    def create(self, request, *args, **kwargs):
        """
        Upsert by hostname so device identity is stable: re-adding a device with
        an existing hostname updates that row (reusing its PK and all references)
        instead of erroring on the unique constraint or creating a duplicate.

        Returns 200 when an existing device was updated, 201 when created.
        (Hostname is globally unique today; once the tenant field lands this
        should key on hostname+tenant — see CLAUDE.md RBAC & Multi-Tenancy.)
        """
        hostname = request.data.get("hostname")
        existing = Device.objects.filter(hostname=hostname).first() if hostname else None
        # Passing instance=existing makes this a full update: the unique
        # validators exclude the instance and the PK is preserved.
        serializer = self.get_serializer(instance=existing, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            serializer.data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )

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

    @extend_schema(
        summary="Time-series telemetry metrics for a device (from InfluxDB)",
        parameters=[
            OpenApiParameter("metric", str, description="cpu|memory|uptime|interfaces|all"),
            OpenApiParameter("period", str, description="1h|6h|24h|7d (default 1h)"),
        ],
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="metrics")
    def metrics(self, request, pk=None):
        """Latest snapshot + windowed time-series for the device's SNMP metrics."""
        from . import metrics_influx
        device = self.get_object()
        data = metrics_influx.query_device_metrics(
            str(device.id),
            request.query_params.get("metric", "all"),
            request.query_params.get("period", "1h"),
        )
        return Response(data)

    @extend_schema(summary="Trigger an immediate SNMP poll of the device", request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="poll-now")
    def poll_now(self, request, pk=None):
        """Republish the device config so the ingest-snmp poller polls it now."""
        from . import snmp_publish
        device = self.get_object()
        if snmp_publish.build_device_payload(device) is None:
            return Response(
                {"status": "not pollable", "device_id": device.id,
                 "detail": "device is inactive or has no SNMP credential profile"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ok = snmp_publish.publish_poll_now(device)
        return Response({"status": "poll triggered" if ok else "publish failed",
                         "device_id": device.id})

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="topology/discover")
    def topology_discover(self, request, pk=None):
        """Discover this device's LLDP neighbors and persist matched links."""
        from . import topology as topo
        from apps.telemetry.discovery import DiscoveryError
        device = self.get_object()
        try:
            found = topo.discover_links(device)
        except DiscoveryError as exc:
            return Response({"error": str(exc), "neighbors": []}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "count": len(found),
            "matched": sum(1 for f in found if f["matched_device_id"]),
            "neighbors": found,
        })

    @action(detail=False, methods=["get"], url_path="topology")
    def topology(self, request):
        """
        Return nodes + edges for the network topology map. Edges come from
        discovered TopologyLink rows. Filters: site, role, device (center) + depth.
        """
        from collections import defaultdict
        from .models import TopologyLink

        params = request.query_params
        devices = Device.objects.select_related("site").filter(
            status__in=[Device.Status.ACTIVE, Device.Status.INACTIVE, Device.Status.MAINTENANCE]
        )
        if params.get("site"):
            devices = devices.filter(site_id=params["site"])
        if params.get("role"):
            devices = devices.filter(notes__icontains=f"Role: {params['role']}")

        dev_ids = set(devices.values_list("id", flat=True))
        links = list(TopologyLink.objects.filter(device_a__in=dev_ids, device_b__in=dev_ids))

        center = params.get("device")
        depth = params.get("depth")
        if center and str(center).isdigit() and int(center) in dev_ids:
            center = int(center)
            adj = defaultdict(set)
            for ln in links:
                adj[ln.device_a_id].add(ln.device_b_id)
                adj[ln.device_b_id].add(ln.device_a_id)
            max_depth = None if (not depth or depth == "all") else int(depth)
            visited, frontier, d = {center}, {center}, 0
            while frontier and (max_depth is None or d < max_depth):
                nxt = set()
                for n in frontier:
                    nxt |= {m for m in adj[n] if m not in visited}
                visited |= nxt
                frontier, d = nxt, d + 1
            dev_ids &= visited
            devices = devices.filter(id__in=dev_ids)
            links = [ln for ln in links if ln.device_a_id in dev_ids and ln.device_b_id in dev_ids]

        def role_of(notes: str) -> str:
            for line in (notes or "").splitlines():
                if line.lower().startswith("role:"):
                    return line.split(":", 1)[1].strip()
            return ""

        nodes = [
            {
                "id": str(d.id), "label": d.hostname, "type": d.platform,
                "site": d.site.name if d.site else None, "status": d.status,
                "role": role_of(d.notes), "risk_score": 0,
                "ip": str(d.ip_address or ""), "vendor": d.vendor or "",
            }
            for d in devices
        ]
        edges = [
            {
                "source": str(ln.device_a_id), "target": str(ln.device_b_id),
                "port_a": ln.port_a, "port_b": ln.port_b,
                "speed_mbps": ln.link_speed_mbps,
                "utilization_pct": 0, "utilization_color": "green",
            }
            for ln in links
        ]
        return Response({"nodes": nodes, "edges": edges})
