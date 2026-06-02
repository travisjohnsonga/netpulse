import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)

from apps.credentials import vault
from apps.credentials.models import CredentialProfile

from . import detect, fingerprint
from .models import Device, DeviceGroup, DiscoveredDevice, DiscoveryJob, Site
from .serializers import (
    DetectPlatformRequestSerializer,
    DetectPlatformResponseSerializer,
    DeviceGroupSerializer,
    DeviceListSerializer,
    DeviceSerializer,
    DiscoveredDeviceSerializer,
    DiscoveryJobSerializer,
    SiteSerializer,
    TestConnectionRequestSerializer,
    TestConnectionResponseSerializer,
)


def _truthy(value) -> bool:
    """Loose truthiness for query-string flags (?show_all=true|1|yes)."""
    return str(value).lower() in ("1", "true", "yes", "on")


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
        # LLDP neighbours from discovered TopologyLink rows (either direction),
        # independent of whether interface discovery populated the per-interface
        # lldp_* fields — so a device with topology links but no per-interface
        # LLDP metadata still shows its neighbours.
        data["lldp_neighbors"] = self._lldp_neighbors(device)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="reachability")
    def reachability(self, request, pk=None):
        """Ping/RTT latency + reachability history (?period=1h|6h|24h|7d)."""
        from . import metrics_influx
        device = self.get_object()
        return Response(metrics_influx.query_reachability(
            str(device.id), request.query_params.get("period", "1h")))

    @action(detail=False, methods=["get"], url_path="reachability-summary")
    def reachability_summary(self, request):
        """Fleet active/unreachable counts over time (?period=1h|6h|24h|7d)."""
        from . import metrics_influx
        return Response(metrics_influx.query_reachability_summary(
            request.query_params.get("period", "1h")))

    @action(detail=False, methods=["get"], url_path="platforms")
    def platforms(self, request):
        """
        Supported device platforms as [{value, label}] for UI dropdowns. Driven
        by Device.Platform, so adding a platform to the model surfaces it in the
        UI with no frontend change.
        """
        return Response([{"value": v, "label": label} for v, label in Device.Platform.choices])

    @staticmethod
    def _lldp_neighbors(device):
        from django.db.models import Q

        from .models import TopologyLink

        out = []
        links = (TopologyLink.objects
                 .filter(Q(device_a=device) | Q(device_b=device))
                 .select_related("device_a", "device_b"))
        for link in links:
            if link.device_a_id == device.id:
                neighbor, local_port, remote_port = link.device_b, link.port_a, link.port_b
            else:
                neighbor, local_port, remote_port = link.device_a, link.port_b, link.port_a
            out.append({
                "local_port": local_port,
                "neighbor_id": neighbor.id,
                "neighbor_hostname": neighbor.hostname,
                "remote_port": remote_port,
                "discovered_via": link.discovered_via,
            })
        return out

    @extend_schema(
        summary="How telemetry is currently being collected (gNMI streaming / SNMP polling)",
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="collection-status")
    def collection_status(self, request, pk=None):
        """
        Report whether gNMI streaming and/or SNMP polling are active for this
        device, based on recent InfluxDB telemetry writes, plus the configured
        intervals and SNMP version. Drives the collection-method badges on the
        device header and Telemetry tab.
        """
        from . import collection_status as cs
        device = self.get_object()
        return Response(cs.build_collection_status(device))

    @extend_schema(summary="Re-run SNMP/SSH enrichment + interface/LLDP discovery", request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="enrich")
    def enrich(self, request, pk=None):
        """
        Re-probe the device in the background to refresh model/OS/serial/platform
        and rediscover interfaces + LLDP links. Returns immediately (202); the
        device record updates in place.
        """
        from .enrich import trigger_enrich
        device = self.get_object()
        scheduled = trigger_enrich(device)
        return Response(
            {"status": "enrichment started" if scheduled else "enrichment unavailable",
             "device_id": device.id,
             "detail": None if scheduled else "No credential profile, or enrichment is disabled."},
            status=status.HTTP_202_ACCEPTED,
        )

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


# ── Discovery ─────────────────────────────────────────────────────────────────

_PLATFORM_VALUES = {p.value for p in Device.Platform}


class DiscoveryJobViewSet(viewsets.ModelViewSet):
    """
    Manage device discovery jobs (passive / topology / active scan / import).

    Creating a job stores it in PENDING; the discovery engine (`run_discovery`)
    executes it and records DiscoveredDevice rows. Discovered devices always land
    in PENDING and require explicit approval — they are never auto-activated.
    Safety: `allowed_subnets` bound probing, `excluded_subnets` must list any
    OT/ICS ranges, `rate_limit_pps` defaults to 10.
    """

    queryset = DiscoveryJob.objects.select_related("seed_device").order_by("-created_at")
    serializer_class = DiscoveryJobSerializer
    filterset_fields = ["method", "status"]
    search_fields = ["name"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        job = serializer.save(created_by=user)
        self._start_discovery(job)

    def update(self, request, *args, **kwargs):
        """Edit a job (PUT/PATCH) — blocked while it is running."""
        job = self.get_object()
        if job.status == DiscoveryJob.Status.RUNNING:
            return Response({"error": "Cannot edit a running job."},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def _reset_and_start(self, job):
        """Reset a job to a fresh pending state and (re)trigger execution."""
        if job.status == DiscoveryJob.Status.RUNNING:
            return Response({"error": "Job is already running."},
                            status=status.HTTP_400_BAD_REQUEST)
        if job.method not in (DiscoveryJob.Method.SCAN, DiscoveryJob.Method.TOPOLOGY):
            return Response({"error": "Only scan and topology jobs can be run."},
                            status=status.HTTP_400_BAD_REQUEST)
        job.status = DiscoveryJob.Status.PENDING
        job.progress_current = job.progress_total = job.ips_scanned = job.devices_found = 0
        job.progress_message = ""
        job.error_message = ""
        job.cancel_requested = False
        job.started_at = job.completed_at = None
        job.save()
        self._start_discovery(job)
        return Response(DiscoveryJobSerializer(job).data)

    @extend_schema(summary="Run a discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Reset progress and execute the job (scan/topology only)."""
        return self._reset_and_start(self.get_object())

    @extend_schema(summary="Restart a finished/cancelled discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def restart(self, request, pk=None):
        """Reset a completed/failed/cancelled job and run it again."""
        return self._reset_and_start(self.get_object())

    @extend_schema(summary="Cancel a pending or running discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Cancel a job. Pending jobs are cancelled immediately; running jobs get a
        cancel flag the engine polls and then stops (status → cancelled).
        """
        from django.utils import timezone

        job = self.get_object()
        if job.status not in (DiscoveryJob.Status.PENDING, DiscoveryJob.Status.RUNNING):
            return Response({"error": "Only pending or running jobs can be cancelled."},
                            status=status.HTTP_400_BAD_REQUEST)
        job.cancel_requested = True
        if job.status == DiscoveryJob.Status.PENDING:
            # Not started yet — cancel right away. (The flag also guards against a
            # worker thread that is just now picking the job up.)
            job.status = DiscoveryJob.Status.CANCELLED
            job.progress_message = "Cancelled by user"
            job.completed_at = timezone.now()
        job.save()
        return Response(DiscoveryJobSerializer(job).data)

    @staticmethod
    def _start_discovery(job):
        """
        Kick off execution for active-scan / topology jobs in a daemon thread so
        the POST returns immediately while the engine runs (status pending →
        running → completed). Passive/import jobs have no engine run.
        """
        from django.conf import settings as dj_settings
        from django.db import transaction

        if not getattr(dj_settings, "DISCOVERY_AUTORUN", True):
            return
        if job.method not in (DiscoveryJob.Method.SCAN, DiscoveryJob.Method.TOPOLOGY):
            return
        from threading import Thread

        job_id = job.id
        # Start only after the job row is committed, so the worker thread's
        # separate DB connection can see it (runs immediately when not in an
        # atomic request).
        transaction.on_commit(
            lambda: Thread(target=DiscoveryJobViewSet._discovery_worker, args=(job_id,), daemon=True).start()
        )

    @staticmethod
    def _discovery_worker(job_id):
        """Thread entrypoint: run the job, then close this thread's DB connection."""
        from django.db import connection
        try:
            DiscoveryJobViewSet._run_discovery(job_id)
        finally:
            connection.close()

    @staticmethod
    def _run_discovery(job_id):
        from django.core.management import call_command

        try:
            # --job maps to the run_discovery command's `job` dest.
            call_command("run_discovery", job=job_id)
        except Exception as exc:  # noqa: BLE001 — record any failure on the job
            DiscoveryJob.objects.filter(id=job_id).update(
                status=DiscoveryJob.Status.FAILED,
                progress_message=str(exc)[:255],
                error_message=str(exc),
            )

    @action(detail=True, methods=["get"])
    def discovered(self, request, pk=None):
        """List devices discovered by this job (filter with ?status=pending)."""
        job = self.get_object()
        qs = job.discovered_devices.all().order_by("-confidence_score", "source_ip")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(DiscoveredDeviceSerializer(qs, many=True).data)

    @extend_schema(summary="Live progress for a discovery job (poll while running)", responses=None)
    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        """Lightweight progress snapshot for polling during a running job."""
        from django.utils import timezone

        job = self.get_object()
        pct = round(min(job.progress_current / job.progress_total * 100, 100)) \
            if job.progress_total > 0 else 0
        if job.started_at:
            end = job.completed_at or timezone.now()
            elapsed = int((end - job.started_at).total_seconds())
        else:
            elapsed = 0
        return Response({
            "status": job.status,
            "progress_pct": pct,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "progress_message": job.progress_message,
            "ips_scanned": job.ips_scanned,
            "devices_found": job.devices_found,
            "elapsed_seconds": elapsed,
            "error_message": job.error_message,
        })


class DiscoveredDeviceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Inspect discovered devices and approve/reject them.

    Approval creates an ACTIVE Device from the fingerprint (never automatic).
    Rejection marks the candidate rejected. Filter by `status` or `job`.
    """

    queryset = DiscoveredDevice.objects.select_related("job", "approved_device").all()
    serializer_class = DiscoveredDeviceSerializer
    filterset_fields = ["status", "job", "device_category"]
    ordering_fields = ["confidence_score", "created_at"]
    ordering = ["-confidence_score"]

    def get_queryset(self):
        qs = super().get_queryset()
        # When DISCOVERY_FILTER_ENDPOINTS is on, hide endpoint/workstation rows
        # from the list (they're still stored and reachable by id) unless the
        # caller passes ?show_all=true (or ?include_endpoints=true). Detail
        # routes and the explicit ?device_category= filter are never hidden.
        if self.action != "list":
            return qs
        from django.conf import settings
        if not getattr(settings, "DISCOVERY_FILTER_ENDPOINTS", True):
            return qs
        params = self.request.query_params
        if _truthy(params.get("show_all")) or _truthy(params.get("include_endpoints")):
            return qs
        if params.get("device_category"):  # explicit category filter wins
            return qs
        return qs.exclude(device_category=DiscoveredDevice.Category.ENDPOINT)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Create an ACTIVE Device from this discovered device (idempotent-safe)."""
        from django.utils import timezone

        from .serializers import existing_device_for

        dd = self.get_object()

        # Already in inventory (already approved, or an existing device matches
        # this IP/hostname): resolve gracefully instead of erroring — link the
        # candidate to the existing device and return it for the UI to navigate.
        existing = existing_device_for(dd)
        if existing:
            if dd.status != DiscoveredDevice.Status.APPROVED:
                dd.status = DiscoveredDevice.Status.APPROVED
                dd.approved_device = existing
                dd.approved_by = request.user if request.user.is_authenticated else None
                dd.approved_at = timezone.now()
                dd.save(update_fields=["status", "approved_device", "approved_by",
                                       "approved_at", "updated_at"])
            return Response(
                {"device": DeviceSerializer(existing).data,
                 "discovered": DiscoveredDeviceSerializer(dd).data,
                 "already_exists": True},
                status=status.HTTP_200_OK,
            )

        hostname = dd.discovered_hostname or f"device-{dd.source_ip}"
        if Device.objects.filter(hostname=hostname).exists():
            hostname = f"{hostname}-{dd.source_ip}"
        # Platform: an explicit override from the request (used when discovery
        # couldn't identify it), else the discovered platform, else the known
        # vendor's default (fortinet → fortios, etc.), else "other".
        from .management.commands.run_discovery import default_platform_for_vendor
        platform = request.data.get("platform") or dd.discovered_platform
        if platform not in _PLATFORM_VALUES:
            platform = default_platform_for_vendor(dd.discovered_vendor) or Device.Platform.OTHER

        # Credentials to attach to the new device: explicit choice in the request,
        # else the job's credential profile (the creds used to discover it).
        cred_profile = dd.job.credential_profile
        cred_id = request.data.get("credential_profile")
        if cred_id not in (None, ""):
            cred_profile = CredentialProfile.objects.filter(pk=cred_id).first()
            if cred_profile is None:
                return Response(
                    {"error": f"Credential profile {cred_id} not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        device = Device.objects.create(
            hostname=hostname,
            ip_address=dd.source_ip,
            management_ip=dd.source_ip,
            vendor=dd.discovered_vendor or "",
            model=dd.discovered_model or "",
            platform=platform,
            os_version=dd.discovered_os or "",
            status=Device.Status.ACTIVE,
            credential_profile=cred_profile,
        )
        dd.status = DiscoveredDevice.Status.APPROVED
        dd.approved_device = device
        dd.approved_by = request.user if request.user.is_authenticated else None
        dd.approved_at = timezone.now()
        dd.save(update_fields=["status", "approved_device", "approved_by", "approved_at", "updated_at"])
        logger.info("Device created from discovery: %s (id=%s, ip=%s)",
                    device.hostname, device.id, device.ip_address)

        # Enrich in the background (SNMP/SSH device info → interfaces → LLDP).
        from .enrich import trigger_enrich
        trigger_enrich(device)

        return Response(
            {"device": DeviceSerializer(device).data,
             "discovered": DiscoveredDeviceSerializer(dd).data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Mark this discovered device rejected (no Device is created)."""
        dd = self.get_object()
        dd.status = DiscoveredDevice.Status.REJECTED
        dd.save(update_fields=["status", "updated_at"])
        return Response(DiscoveredDeviceSerializer(dd).data)
