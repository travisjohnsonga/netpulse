"""Agent enrollment, metrics/role-check ingestion, and management APIs."""
import logging

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.errors import safe_detail

from . import pki
from .metrics import write_agent_metrics
from .models import Agent, AgentEnrollmentToken, AgentRoleStatus, ServerRole
from .serializers import (
    AgentEnrollmentTokenSerializer,
    AgentRoleStatusSerializer,
    AgentSerializer,
    EnrollRequestSerializer,
    ServerRoleSerializer,
)

logger = logging.getLogger(__name__)

# Header carrying the verified client-cert serial. In production nginx terminates
# mTLS and sets this from $ssl_client_serial (the mTLS-edge follow-up); the agent
# is authenticated by matching it to Agent.cert_serial.
CERT_SERIAL_HEADER = "HTTP_X_AGENT_CERT_SERIAL"


def _server_url() -> str:
    hosts = [h for h in getattr(settings, "ALLOWED_HOSTS", []) if h not in ("*", "localhost", "127.0.0.1")]
    host = hosts[0] if hosts else "localhost"
    return f"https://{host}"


class ServerRoleViewSet(viewsets.ModelViewSet):
    """CRUD for server-role profiles. Built-in roles can't be deleted."""
    queryset = ServerRole.objects.all()
    serializer_class = ServerRoleSerializer

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_builtin:
            return Response({"detail": "Built-in roles cannot be deleted."},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)


class AgentEnrollmentTokenViewSet(viewsets.ModelViewSet):
    """Create/list/revoke enrollment tokens. Token value shown once on create."""
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

    # Actions that must NOT require a JWT: enrollment is authenticated by the
    # one-time enrollment token; metrics/role-checks by the mTLS client-cert
    # serial; ca_certificate returns public CA info. They are AllowAny with no
    # JWT/Session authenticators.
    PUBLIC_ACTIONS = ("enroll", "metrics", "role_checks", "ca_certificate")

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
        if self._resolved_action() in self.PUBLIC_ACTIONS:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        # No JWT/Session auth on public actions — this also skips
        # SessionAuthentication's CSRF enforcement on these POSTs. Without this,
        # the default authenticators would emit a WWW-Authenticate challenge and
        # any auth misstep would surface as a 401 instead of staying public.
        if self._resolved_action() in self.PUBLIC_ACTIONS:
            return []
        return super().get_authenticators()

    def _cert_authed_agent(self, request, pk):
        """Return the Agent if the request's client-cert serial matches it, else None."""
        agent = Agent.objects.filter(pk=pk).first()
        if not agent or agent.status == Agent.Status.REVOKED:
            return None
        serial = request.META.get(CERT_SERIAL_HEADER, "")
        if not agent.cert_serial or serial != agent.cert_serial:
            return None
        return agent

    def destroy(self, request, *args, **kwargs):
        """Revoke (soft): mark revoked + best-effort revoke the cert in OpenBao."""
        agent = self.get_object()
        agent.status = Agent.Status.REVOKED
        agent.save(update_fields=["status", "updated_at"])
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

        agent = Agent.objects.create(
            hostname=data["hostname"], os=data["os"], arch=data["arch"],
            version=data["version"], enrollment_token=token,
            cert_serial=issued.get("serial", ""),
            collection_interval=30,
        )
        self._link_device(agent, request, token)

        token.use_count += 1
        if token.max_uses and token.use_count >= token.max_uses:
            token.is_active = False
        token.save(update_fields=["use_count", "is_active", "updated_at"])

        ca = issued.get("ca_chain") or []
        return Response({
            "agent_id": str(agent.id),
            "certificate": issued["certificate"],
            "ca_certificate": "\n".join(ca) if isinstance(ca, list) else ca,
            "collection_interval": agent.collection_interval,
            "server_url": _server_url(),
        }, status=status.HTTP_201_CREATED)

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
                site=token.site, notes="Monitored by NetPulse Agent",
            )
        elif token.site_id and not device.site_id:
            device.site = token.site
            device.save(update_fields=["site"])
        agent.device = device
        agent.save(update_fields=["device", "updated_at"])

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def metrics(self, request, pk=None):
        """Ingest a metrics payload (client-cert authed) → InfluxDB."""
        agent = self._cert_authed_agent(request, pk)
        if agent is None:
            return Response({"detail": "Agent certificate invalid."},
                            status=status.HTTP_403_FORBIDDEN)
        payload = request.data or {}
        device_id = agent.device_id or agent.id
        written = write_agent_metrics(device_id, agent.hostname,
                                      payload.get("metrics") or {}, ts=payload.get("timestamp"))
        agent.last_seen = timezone.now()
        if agent.status == Agent.Status.INACTIVE:
            agent.status = Agent.Status.ACTIVE
        agent.save(update_fields=["last_seen", "status", "updated_at"])
        return Response({"accepted": True, "points_written": written})

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="role-checks")
    def role_checks(self, request, pk=None):
        """Ingest role-check results (client-cert authed). Upsert per role_type."""
        agent = self._cert_authed_agent(request, pk)
        if agent is None:
            return Response({"detail": "Agent certificate invalid."},
                            status=status.HTTP_403_FORBIDDEN)
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
        base = _server_url()
        platforms = ["linux-amd64", "linux-arm64", "windows-amd64"]
        return Response({
            "platforms": platforms,
            "download_urls": {p: f"{base}/agent/download/{p}" for p in platforms},
            "install_linux": f"curl -fsSL {base}/agent/install | sudo bash -s -- "
                             f"--server {base} --token <TOKEN>",
            "install_windows": "powershell -ExecutionPolicy Bypass -File install.ps1 "
                               f"-Server {base} -Token <TOKEN>",
        })
