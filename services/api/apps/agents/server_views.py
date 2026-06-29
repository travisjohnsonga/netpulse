"""Servers API (`/api/servers/`) — agent-monitored servers.

These are the same underlying ``Agent`` rows as ``/api/agents/`` but framed as
servers: role assignment (manual + auto-detect) here, plus list/detail + metrics
(see the Servers page work). Admin/JWT-authed via the default permission.
"""
from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alerts.models import AlertEvent
from apps.core.permissions import CapabilityViewSetMixin

from .detection import auto_detect_roles
from .metrics_read import detail_metrics, metric_history
from .models import Agent, AgentRole, ServerRole
from .serializers import (
    AgentConfigSerializer, AgentLivenessSerializer, AssignedRoleSerializer,
    ServerSerializer,
)


def _merge_config(stored: dict, patch: dict) -> dict:
    """Merge a validated (partial) config PATCH onto the stored desired_config.
    Sections not present in the patch are left untouched; within a section the
    provided keys override."""
    out = dict(stored or {})
    if "collection" in patch:
        out["collection"] = {**(out.get("collection") or {}), **patch["collection"]}
    if "interval_seconds" in patch:
        out["interval_seconds"] = patch["interval_seconds"]
    if "disk" in patch:
        out["disk"] = {**(out.get("disk") or {}), **patch["disk"]}
    if "logs" in patch:
        out["logs"] = {**(out.get("logs") or {}), **patch["logs"]}
    if "stability" in patch:
        out["stability"] = {**(out.get("stability") or {}), **patch["stability"]}
    if "functional" in patch:
        out["functional"] = {**(out.get("functional") or {}), **patch["functional"]}
    return out


class ServerViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """List/retrieve agent-monitored servers, their metrics, and role assignments."""
    view_capability = "agent:view"
    write_capability = "agent:edit"

    queryset = (Agent.objects.exclude(status=Agent.Status.REVOKED)
                .select_related("device", "device__site")
                .prefetch_related("assigned_roles__role"))
    serializer_class = ServerSerializer

    def get_queryset(self):
        """Optionally scope the list to one site (?site=<id>). Servers link to a
        site via their device (Agent.device → Device.site), so this filters on
        device__site — matching how the site server-counts are computed."""
        qs = super().get_queryset()
        site = self.request.query_params.get("site")
        if site:
            qs = qs.filter(device__site_id=site)
        return qs

    def retrieve(self, request, *args, **kwargs):
        """Full server detail: list fields + current per-core/mount/iface metrics
        + the 5 most recent alerts for the linked device."""
        server = self.get_object()
        data = self.get_serializer(server).data
        device_id = str(server.device_id or server.id)
        data["detail_metrics"] = detail_metrics(device_id)
        data["recent_alerts"] = self._recent_alerts(server)
        data["watched_services"] = self._watched_services(server)
        data["network"] = self._network_state(server)
        return Response(data)

    @staticmethod
    def _network_state(server) -> dict:
        """Collector-originated network reachability for the server-detail
        "Network" chip — distinct from the agent's self-reported liveness.

        - ``probed=False`` when the agent has no routable IP (Agent.last_ip is
          missing/loopback/synthetic) → the UI shows "not network-probed", NOT a
          false "unreachable" (#133 lesson).
        - otherwise ``reachable`` is driven by the standing Host-unreachable alert
          (fired by the reachability monitor's collector probe). The RTT for the
          chip comes from the same ping-summary the Servers list uses (by
          device_id), so it isn't recomputed here."""
        from apps.devices.management.commands.run_reachability_monitor import is_pingable_ip
        if not is_pingable_ip(server.last_ip):
            return {"probed": False, "reachable": None, "ip": server.last_ip,
                    "reason": "no routable host IP reported by the agent yet"}
        open_unreachable = AlertEvent.objects.filter(
            state=AlertEvent.State.FIRING,
            labels__alert_type="host_unreachable",
            labels__agent_id=str(server.id),
        ).exists()
        # Current RTT from the SAME cached ping-summary the Servers list uses
        # (no extra InfluxDB query) — keyed by device_id. Absent → chip shows
        # "reachable" without a number.
        rtt = None
        if server.device_id:
            from django.core.cache import cache
            for row in (cache.get("ping_summary") or []):
                if row.get("device_id") == server.device_id:
                    rtt = row.get("current_ms")
                    break
        return {"probed": True, "reachable": not open_unreachable,
                "ip": server.last_ip, "rtt_ms": rtt}

    @staticmethod
    def _watched_services(server) -> dict:
        """Stability view: the configured watch list + each watched service's
        current health (up/down, last change, down-since, 24h restarts)."""
        import datetime as _dt
        from django.utils import timezone
        configured = (server.effective_config().get("stability", {}) or {}).get("services", [])
        now = timezone.now()

        def restarts_24h(rs):
            cutoff = now - _dt.timedelta(hours=24)
            n = 0
            for t in rs or []:
                try:
                    if _dt.datetime.fromisoformat(t) >= cutoff:
                        n += 1
                except (ValueError, TypeError):
                    continue
            return n

        # Friendly names come from the general running-services list (the agent
        # collects DisplayName there); look them up by name. Blank for a watched
        # service that isn't currently in that list (e.g. stopped, or the
        # 'services' toggle is off) — the UI falls back to the actual name.
        display = {}
        for s in (server.reported_services or []):
            if isinstance(s, dict) and s.get("name") and s.get("display_name"):
                display[s["name"]] = s["display_name"]

        rows = [{
            "name": ws.name, "display_name": display.get(ws.name, ""),
            "running": ws.running, "state": ws.state,
            "last_change_at": ws.last_change_at, "down_since": ws.down_since,
            "restarts_24h": restarts_24h(ws.restarts), "collected_at": ws.collected_at,
        } for ws in server.watched_services.all()]
        return {"configured": configured, "statuses": rows}

    @action(detail=True, methods=["get"], url_path="metrics/history")
    def metrics_history(self, request, pk=None):
        """Windowed time-series for charting. ?metric=cpu|memory|disk|load|network
        &range=1h|6h|24h|7d."""
        server = self.get_object()
        device_id = str(server.device_id or server.id)
        metric = request.query_params.get("metric", "cpu")
        rng = request.query_params.get("range", "1h")
        return Response(metric_history(device_id, metric, rng))

    @staticmethod
    def _recent_alerts(server) -> list[dict]:
        if not server.device_id:
            return []
        events = (AlertEvent.objects.filter(labels__device_id=server.device_id)
                  .select_related("rule").order_by("-created_at")[:5])
        return [{
            "id": e.id, "name": e.rule.name, "severity": e.rule.severity,
            "state": e.state, "summary": (e.annotations or {}).get("summary", ""),
            "created_at": e.created_at,
        } for e in events]

    @action(detail=True, methods=["get", "post"])
    def roles(self, request, pk=None):
        """GET: assigned roles + latest check status. POST {role_id}: assign."""
        server = self.get_object()
        if request.method == "POST":
            role = ServerRole.objects.filter(pk=request.data.get("role_id")).first()
            if not role:
                return Response({"detail": "role_id not found."},
                                status=status.HTTP_400_BAD_REQUEST)
            assignment, created = AgentRole.objects.get_or_create(
                agent=server, role=role,
                defaults={"assigned_by": request.user if request.user.is_authenticated else None},
            )
            return Response(AssignedRoleSerializer(assignment).data,
                            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        assignments = server.assigned_roles.select_related("role").all()
        return Response(AssignedRoleSerializer(assignments, many=True).data)

    @action(detail=True, methods=["post"], url_path="site")
    def change_site(self, request, pk=None):
        """Reassign the server to a different site (or unassign with site_id null).
        The site lives on the linked Device; gated by agent:edit and audit-logged.
        Used for servers that move sites or were enrolled with the wrong/blank
        site (the common case is set at enrollment via the token's site)."""
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        from apps.devices.models import Site

        server = self.get_object()
        device = server.device
        if device is None:
            # A device-less agent (e.g. enrolled behind a shared-IP proxy) used to
            # 400 here. Create/link its Device on demand so site-assign just works.
            from .device_link import ensure_agent_device
            device = ensure_agent_device(server, request=request)
            if device is None:
                return Response(
                    {"detail": "Could not create a device for this server."},
                    status=status.HTTP_400_BAD_REQUEST)

        site_id = request.data.get("site_id", None)
        new_site = None
        if site_id not in (None, "", 0, "0"):
            new_site = Site.objects.filter(pk=site_id).first()
            if new_site is None:
                return Response({"detail": "site_id not found."}, status=status.HTTP_400_BAD_REQUEST)

        old_site = device.site
        if (old_site.id if old_site else None) != (new_site.id if new_site else None):
            device.site = new_site
            device.save(update_fields=["site"])
            log_event(
                AuditLog.EventType.AGENT_SITE_CHANGED, request=request, target=server,
                description=(f"Server {server.hostname} site changed: "
                             f"{old_site.name if old_site else '—'} → "
                             f"{new_site.name if new_site else '—'}"),
                metadata={"old_site": old_site.name if old_site else None,
                          "new_site": new_site.name if new_site else None},
            )
        return Response(self.get_serializer(server).data)

    @action(detail=True, methods=["get", "patch"], url_path="config")
    def config(self, request, pk=None):
        """View (GET) or edit (PATCH) the agent's DESIRED config — collection
        toggles, interval, and monitored-drive include/exclude. The agent pulls
        it on its next metrics check-in (~30s) and applies it then, so a PATCH is
        NOT instant (the UI shows a "pending, applies on next check-in" state).
        GET is gated by agent:view, PATCH by agent:edit (CapabilityViewSetMixin);
        changes are audit-logged."""
        from apps.core.audit import log_event
        from apps.core.models import AuditLog

        server = self.get_object()
        if request.method == "GET":
            return Response(server.effective_config())

        ser = AgentConfigSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        before = server.effective_config()
        server.desired_config = _merge_config(server.desired_config or {}, ser.validated_data)
        server.save(update_fields=["desired_config", "updated_at"])
        after = server.effective_config()
        if after != before:
            log_event(
                AuditLog.EventType.AGENT_CONFIG_CHANGED, request=request, target=server,
                description=f"Agent config changed for {server.hostname}",
                metadata={"before": before, "after": after},
            )
        return Response(after)

    @action(detail=True, methods=["patch"], url_path="liveness")
    def liveness(self, request, pk=None):
        """Per-agent liveness-alert config: offline_threshold_seconds (null =
        global AGENT_OFFLINE_SECONDS) and liveness_alerts_enabled (False
        suppresses the offline alert + resolves any open one — for a host that
        legitimately sleeps, e.g. the lab). Gated by agent:edit; audit-logged."""
        from apps.core.audit import log_event
        from apps.core.models import AuditLog

        server = self.get_object()
        ser = AgentLivenessSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        changed = []
        if "offline_threshold_seconds" in data:
            server.offline_threshold_seconds = data["offline_threshold_seconds"]
            changed.append("offline_threshold_seconds")
        if "liveness_alerts_enabled" in data:
            server.liveness_alerts_enabled = data["liveness_alerts_enabled"]
            changed.append("liveness_alerts_enabled")
        if changed:
            server.save(update_fields=changed + ["updated_at"])
            log_event(
                AuditLog.EventType.AGENT_CONFIG_CHANGED, request=request, target=server,
                description=f"Agent liveness config changed for {server.hostname}",
                metadata={k: getattr(server, k) for k in changed})
        return Response(self.get_serializer(server).data)

    @action(detail=True, methods=["patch"], url_path="alerting")
    def alerting(self, request, pk=None):
        """Per-server alert silencing — writes the agent's Device flags
        (`alerting_enabled` = observe-only, `silenced_until` = timed mute, auto-
        resumes). NOTIFICATION-only — AlertEvents still generate. agent:edit; audited."""
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        from apps.devices.serializers import DeviceAlertingSerializer

        server = self.get_object()
        if not server.device_id:
            return Response({"detail": "Server has no linked device."},
                            status=status.HTTP_400_BAD_REQUEST)
        ser = DeviceAlertingSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        device = server.device
        changed = [f for f in ("alerting_enabled", "silenced_until") if f in ser.validated_data]
        for f in changed:
            setattr(device, f, ser.validated_data[f])
        if changed:
            device.save(update_fields=changed + ["updated_at"])
            log_event(AuditLog.EventType.AGENT_CONFIG_CHANGED, request=request, target=server,
                      description=f"Alert silencing changed for {server.hostname}",
                      metadata={k: str(getattr(device, k)) for k in changed})
        return Response(self.get_serializer(server).data)

    @action(detail=True, methods=["delete"], url_path=r"roles/(?P<role_id>[^/.]+)")
    def remove_role(self, request, pk=None, role_id=None):
        """Unassign a role from the server."""
        server = self.get_object()
        deleted, _ = AgentRole.objects.filter(agent=server, role_id=role_id).delete()
        if not deleted:
            return Response({"detail": "Role not assigned."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="detect-roles")
    def detect_roles(self, request, pk=None):
        """Auto-detect candidate roles from the server's reported running services."""
        server = self.get_object()
        return Response({"detected": auto_detect_roles(server)})
