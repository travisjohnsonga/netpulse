"""Agent enrollment, metrics/role-check ingestion, and management APIs."""
import logging

from django.conf import settings
from django.db import IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core.errors import safe_detail
from apps.core.http import NoStoreResponseMixin, add_no_store
from apps.core.permissions import CapabilityViewSetMixin, HasCapability

from . import pki
from .authentication import AgentCertAuthentication
from .metrics import write_agent_metrics
from .models import Agent, AgentEnrollmentToken, AgentRole, AgentRoleStatus, ServerRole
from .serializers import (
    AgentEnrollmentTokenSerializer,
    AgentRoleStatusSerializer,
    AgentSerializer,
    EnrollRequestSerializer,
    ServerRoleSerializer,
)

logger = logging.getLogger(__name__)


def _server_url(request=None) -> str:
    """Public base URL an agent should use to reach this server.

    Derived from how the agent ACTUALLY reached us — the request Host header,
    honoring nginx's X-Forwarded-Proto via SECURE_PROXY_SSL_HEADER — so it
    reflects the operator-supplied address rather than guessing from
    ALLOWED_HOSTS. An explicit AGENT_SERVER_URL setting overrides it for
    split-DNS / published-hostname setups. NEVER returns localhost: that is
    useless to a remote agent (the bug this fixes)."""
    explicit = (getattr(settings, "AGENT_SERVER_URL", "") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    if request is not None:
        host = request.get_host()
        if host:
            # Always https: agents reach the platform over TLS only (nginx
            # redirects 80→443 and the mTLS metrics push requires it). The bug
            # was the host (localhost), not the scheme — echo the real host.
            return f"https://{host}"
    # Fallback only when there's no request (e.g. a management command): a real
    # configured host, never localhost.
    hosts = [h for h in getattr(settings, "ALLOWED_HOSTS", []) if h not in ("*", "localhost", "127.0.0.1")]
    return f"https://{hosts[0]}" if hosts else ""


class ServerRoleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD for server-role profiles. Built-in roles can't be deleted."""
    view_capability = "agent:view"
    write_capability = "agent:edit"

    queryset = ServerRole.objects.all()
    serializer_class = ServerRoleSerializer

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_builtin:
            return Response({"detail": "Built-in roles cannot be deleted."},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)


class AgentEnrollmentTokenViewSet(NoStoreResponseMixin, CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Create/list/revoke enrollment tokens. Token value shown once on create.
    no-store on every response: the create response carries the one-time token."""
    view_capability = "agent:view"
    write_capability = "agent:edit"

    queryset = AgentEnrollmentToken.objects.all()
    serializer_class = AgentEnrollmentTokenSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)
        serializer.instance._reveal_token = True  # full token in the create response


class AgentViewSet(viewsets.ReadOnlyModelViewSet):
    """List/retrieve agents; enroll (public, token-auth); ingest metrics/role
    checks (client-cert-auth); revoke."""
    queryset = Agent.objects.all().select_related("device", "device__site")
    serializer_class = AgentSerializer

    # Public (no JWT/Session auth): enrollment is authed by the one-time token;
    # ca_certificate returns public CA info.
    PUBLIC_ACTIONS = ("enroll", "ca_certificate")
    # mTLS-authed ingestion: authenticated by the nginx-verified client-cert
    # serial via AgentCertAuthentication (request.user is the Agent).
    CERT_ACTIONS = ("metrics", "role_checks")

    def _resolved_action(self):
        # get_authenticators() runs inside initialize_request(), BEFORE
        # ViewSetMixin assigns self.action — so resolve it ourselves from the
        # action_map + request method (both already set by the view closure).
        # get_permissions() runs later, when self.action IS set.
        action = getattr(self, "action", None)
        if action is None:
            method = getattr(getattr(self, "request", None), "method", "") or ""
            action = (getattr(self, "action_map", None) or {}).get(method.lower())
        return action

    def get_permissions(self):
        action = self._resolved_action()
        if action in self.PUBLIC_ACTIONS:
            return [AllowAny()]
        if action in self.CERT_ACTIONS:
            return [IsAuthenticated()]
        if action == "destroy":
            return [HasCapability("agent:edit")()]
        # list, retrieve, roles, download.
        return [HasCapability("agent:view")()]

    def get_authenticators(self):
        # Public actions: no authenticators (also skips SessionAuthentication's
        # CSRF on these POSTs). Ingestion: only the mTLS cert authenticator —
        # never JWT/Session.
        action = self._resolved_action()
        if action in self.PUBLIC_ACTIONS:
            return []
        if action in self.CERT_ACTIONS:
            return [AgentCertAuthentication()]
        return super().get_authenticators()

    def destroy(self, request, *args, **kwargs):
        """Revoke (soft): mark revoked + best-effort revoke the cert in OpenBao."""
        agent = self.get_object()
        agent.status = Agent.Status.REVOKED
        agent.save(update_fields=["status", "updated_at"])
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.AGENT_REVOKED, request=request, target=agent,
                  description=f"Agent revoked: {agent.hostname}")
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=EnrollRequestSerializer, responses=None)
    @action(detail=False, methods=["post"])
    def enroll(self, request):
        """Enroll an agent: validate the token, sign its CSR, create the Agent +
        a linked Device, and return the certificate + collection settings."""
        ser = EnrollRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        token = AgentEnrollmentToken.objects.filter(token=data["enrollment_token"]).first()
        if not token or not token.is_valid():
            return Response({"detail": "Invalid or expired enrollment token."},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            issued = pki.issue_agent_certificate(data["hostname"], data["csr"])
        except pki.AgentPKIError as exc:
            return Response({"detail": safe_detail(exc, logger, "agent enroll",
                            public="Certificate issuance failed. Check OpenBao PKI setup.")},
                            status=status.HTTP_502_BAD_GATEWAY)

        # Graceful (re-)enrollment: re-running the installer on a host that's
        # already enrolled rotates the cert on the EXISTING agent record instead
        # of creating a duplicate (which would also collide on the OneToOne
        # device link → 500). Revoked agents are left alone — a fresh record is
        # created so a revoked host isn't silently resurrected.
        agent = (Agent.objects.filter(hostname=data["hostname"])
                 .exclude(status=Agent.Status.REVOKED).first())
        created = agent is None
        if created:
            agent = Agent(hostname=data["hostname"], collection_interval=30)
        agent.os = data["os"]
        agent.arch = data["arch"]
        agent.version = data["version"]
        agent.enrollment_token = token
        agent.cert_serial = issued.get("serial", "")
        agent.status = Agent.Status.ACTIVE
        try:
            agent.save()
            self._link_device(agent, request, token)
        except IntegrityError:
            # Expected condition (e.g. a stale unique link we couldn't reconcile)
            # — never surface a 500. Point the operator at the fix.
            existing = Agent.objects.filter(hostname=data["hostname"]).first()
            return Response({"detail": (
                "This host already has an enrolled agent. Revoke it in "
                "Settings → Agents, then re-run the installer."
                + (f" Existing agent ID: {existing.id}." if existing else "")
            )}, status=status.HTTP_409_CONFLICT)

        token.use_count += 1
        if token.max_uses and token.use_count >= token.max_uses:
            token.is_active = False
        token.save(update_fields=["use_count", "is_active", "updated_at"])

        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(
            AuditLog.EventType.AGENT_ENROLLED, request=request, target=agent,
            description=(f"Agent {'re-' if not created else ''}enrolled: "
                         f"{agent.hostname} ({agent.os}, {agent.arch})"),
            metadata={"os": agent.os, "arch": agent.arch, "version": agent.version},
        )

        ca = issued.get("ca_chain") or []
        # no-store: this response carries the signed client certificate.
        return add_no_store(Response({
            "agent_id": str(agent.id),
            "certificate": issued["certificate"],
            "ca_certificate": "\n".join(ca) if isinstance(ca, list) else ca,
            "collection_interval": agent.collection_interval,
            "server_url": _server_url(request),
            "re_enrolled": not created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK))

    def _link_device(self, agent, request, token):
        from apps.devices.models import Device
        ip = request.META.get("REMOTE_ADDR") or None
        device = Device.objects.filter(hostname=agent.hostname).first()
        if device is None:
            # ip_address is required+unique; only create when we have a usable IP
            # that isn't already owned (agents behind one NAT share a source IP —
            # the agent still enrolls, just without an auto-created device row).
            if not ip or Device.objects.filter(ip_address=ip).exists():
                return
            device = Device.objects.create(
                hostname=agent.hostname, ip_address=ip, management_ip=ip,
                platform=Device.Platform.OTHER, status=Device.Status.ACTIVE,
                site=token.site, notes="Monitored by spane Agent",
            )
        elif token.site_id and not device.site_id:
            device.site = token.site
            device.save(update_fields=["site"])
        if agent.device_id == device.id:
            return
        # device.agent is a OneToOne — transfer it off any prior agent (e.g. a
        # revoked enrollment for this host) so the re-enrolling agent can claim it.
        prior = Agent.objects.filter(device=device).exclude(pk=agent.pk).first()
        if prior:
            prior.device = None
            prior.save(update_fields=["device", "updated_at"])
        agent.device = device
        agent.save(update_fields=["device", "updated_at"])

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def metrics(self, request, pk=None):
        """Ingest a metrics payload → InfluxDB. Authenticated by the agent's
        mTLS client cert (AgentCertAuthentication); request.user is the Agent.
        Identity comes from the verified cert, so the URL pk is informational."""
        agent = request.user
        payload = request.data or {}
        metrics = payload.get("metrics") or {}
        device_id = agent.device_id or agent.id
        written = write_agent_metrics(device_id, agent.hostname, metrics, ts=payload.get("timestamp"))
        update_fields = ["last_seen", "status", "updated_at"]
        # Capture running service names (when the agent collects them) for role
        # auto-detection. Accept a list of names or {name, running?} dicts.
        services = metrics.get("services")
        if isinstance(services, list):
            names = []
            for s in services:
                if isinstance(s, str):
                    names.append(s)
                elif isinstance(s, dict) and s.get("name") and s.get("running", True):
                    names.append(s["name"])
            agent.reported_services = names
            update_fields.append("reported_services")
        # Keep the stored version in sync with the CURRENTLY RUNNING agent — the
        # agent reports its build in every payload (top-level "version"), and
        # upgrades happen after enrollment. Only update on a non-empty value so a
        # payload that omits it never blanks a good stored version.
        reported_version = (payload.get("version") or "").strip()
        if reported_version and reported_version != agent.version:
            agent.version = reported_version
            update_fields.append("version")
        agent.last_seen = timezone.now()
        if agent.status == Agent.Status.INACTIVE:
            agent.status = Agent.Status.ACTIVE
        agent.save(update_fields=update_fields)
        # Push the server-authoritative role assignments back in the response so
        # the agent can auto-enable role checks without a manual config edit.
        # de-dup while preserving assignment order.
        seen: set[str] = set()
        assigned_roles = [
            rt for rt in agent.assigned_roles.select_related("role")
                              .values_list("role__role_type", flat=True)
            if rt and not (rt in seen or seen.add(rt))
        ]
        return Response({
            "accepted": True,
            "points_written": written,
            "assigned_roles": assigned_roles,
            "collection_config": {
                # Keep reporting running service names so role auto-detection works.
                "services": True,
                # Turn role checks on as soon as at least one role is assigned.
                "role_checks_enabled": bool(assigned_roles),
            },
        })

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="role-checks")
    def role_checks(self, request, pk=None):
        """Ingest role-check results (mTLS cert authed; request.user is the
        Agent). Upsert per role_type."""
        agent = request.user
        results = (request.data or {}).get("roles") or []
        now = timezone.now()
        for r in results if isinstance(results, list) else []:
            if not isinstance(r, dict) or not r.get("role"):
                continue
            AgentRoleStatus.objects.update_or_create(
                agent=agent, role_type=r["role"],
                defaults={"services": r.get("services") or [], "ports": r.get("ports") or [],
                          "custom": r.get("custom") or [], "collected_at": now},
            )
            # Method 3: roles declared in the agent's config (it's reporting checks
            # for them) auto-create the assignment so they show on the Roles tab.
            role = ServerRole.objects.filter(role_type=r["role"]).first()
            if role:
                AgentRole.objects.get_or_create(
                    agent=agent, role=role, defaults={"auto_detected": True})
        agent.last_seen = now
        agent.save(update_fields=["last_seen", "updated_at"])
        return Response({"accepted": True, "roles": len(results)})

    @extend_schema(responses=None)
    @action(detail=True, methods=["get"])
    def roles(self, request, pk=None):
        """Return the agent's latest role-check status per role."""
        agent = self.get_object()
        statuses = agent.role_statuses.all()
        return Response(AgentRoleStatusSerializer(statuses, many=True).data)

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"], url_path="ca-certificate")
    def ca_certificate(self, request):
        """Return the agent PKI CA certificate (PEM, public). Agents fetch this
        during enrollment and store it as ca.crt to verify the server."""
        try:
            pem = pki.read_ca_certificate()
        except pki.AgentPKIError as exc:
            return Response({"detail": safe_detail(exc, logger, "agent ca-cert",
                            public="CA certificate unavailable. Check OpenBao PKI setup.")},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return HttpResponse(pem, content_type="application/x-pem-file")

    @extend_schema(responses=None)
    @action(detail=False, methods=["get"])
    def download(self, request):
        """Agent install info + per-platform download paths (binaries served by CI/static)."""
        base = _server_url(request)
        platforms = ["linux-amd64", "linux-arm64", "windows-amd64"]
        return Response({
            "platforms": platforms,
            "download_urls": {p: f"{base}/agent/download/{p}" for p in platforms},
            "install_linux": f"curl -fsSL {base}/agent/install | sudo bash -s -- "
                             f"--server {base} --token <TOKEN>",
            "install_windows": "powershell -ExecutionPolicy Bypass -File install.ps1 "
                               f"-Server {base} -Token <TOKEN>",
        })
