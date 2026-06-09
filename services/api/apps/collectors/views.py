"""Collector CRUD + remote-collector enrollment / heartbeat.

Enrollment exchange (bootstrap, no prior auth — guarded by a one-time token):
  admin POST /api/collectors/           → creates a pending remote collector,
                                           returns the one-time enrollment_token
  agent POST /api/collectors/enroll/    → token → { api_key (once), mTLS cert,
                                           nats_account }
  agent POST /api/collectors/heartbeat/ → api-key-authenticated liveness

Secrets (API key, enrollment token, cert key) are never returned except the
single bootstrap response, and are stored only as bcrypt hashes / in OpenBao.
"""
from __future__ import annotations

import logging
import uuid

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.devices.serializers import DeviceListSerializer

from . import auth, pki
from .models import Collector
from .serializers import CollectorSerializer

logger = logging.getLogger(__name__)


def authenticate_collector(request) -> Collector | None:
    """Resolve + verify a collector from its X-Collector-Id / X-Collector-Key headers."""
    cid = request.headers.get("X-Collector-Id")
    key = request.headers.get("X-Collector-Key")
    if not cid or not key:
        return None
    collector = Collector.objects.filter(pk=cid).exclude(status=Collector.Status.REVOKED).first()
    if not collector or not collector.api_key_hash:
        return None
    return collector if auth.verify_secret(key, collector.api_key_hash) else None


class CollectorViewSet(viewsets.ModelViewSet):
    queryset = Collector.objects.all()
    serializer_class = CollectorSerializer
    filterset_fields = ["status", "collector_type"]
    search_fields = ["name", "remote_ip", "hostname"]
    ordering_fields = ["last_seen_at", "created_at", "status", "collector_type"]

    def get_permissions(self):
        # The bootstrap exchange authenticates itself (one-time token / API key).
        if self.action in ("enroll", "heartbeat"):
            return [AllowAny()]
        return super().get_permissions()

    def get_throttles(self):
        # Brute-force guard on the unauthenticated bootstrap endpoints.
        if self.action in ("enroll", "heartbeat"):
            t = ScopedRateThrottle()
            t.scope = "auth"
            return [t]
        return super().get_throttles()

    def create(self, request, *args, **kwargs):
        """Create a pending REMOTE collector and return its one-time enrollment token."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = auth.generate_enrollment_token()
        collector = serializer.save(
            collector_type=Collector.CollectorType.REMOTE,
            status=Collector.Status.PENDING,
            enrollment_token_hash=auth.hash_secret(token),
            # api_key_hash is required + unique but no key is issued until enroll;
            # a per-row placeholder keeps the constraint satisfied meanwhile.
            api_key_hash=f"pending-{uuid.uuid4().hex}",
        )
        data = self.get_serializer(collector).data
        # Returned exactly once — the agent needs it to bootstrap.
        data["enrollment_token"] = token
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def enroll(self, request):
        """Exchange a one-time enrollment token for an API key + mTLS cert."""
        token = (request.data or {}).get("enrollment_token", "")
        if not token:
            return Response({"error": "enrollment_token is required"}, status=status.HTTP_400_BAD_REQUEST)
        # Match the token against pending, not-yet-enrolled collectors.
        candidate = None
        for c in Collector.objects.filter(
            collector_type=Collector.CollectorType.REMOTE, enrolled_at__isnull=True
        ).exclude(enrollment_token_hash=""):
            if auth.verify_secret(token, c.enrollment_token_hash):
                candidate = c
                break
        if candidate is None:
            return Response({"error": "invalid or already-used enrollment token"},
                            status=status.HTTP_401_UNAUTHORIZED)

        api_key = auth.generate_api_key()
        now = timezone.now()
        candidate.api_key_hash = auth.hash_secret(api_key)
        candidate.api_key_issued_at = now
        candidate.enrolled_at = now
        candidate.enrollment_token_hash = ""  # one-time use — consume it
        candidate.nats_account = f"collector-{candidate.id}"
        # Best-effort identity details from the agent.
        for field in ("hostname", "version"):
            if request.data.get(field):
                setattr(candidate, field, str(request.data[field])[:255])
        if isinstance(request.data.get("capabilities"), dict):
            candidate.capabilities = request.data["capabilities"]
        candidate.remote_ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR")
        )
        candidate.save()

        # mTLS cert — best-effort; collector is cert-pending if PKI isn't up yet.
        cert = pki.issue_collector_cert(candidate)

        body = {
            "collector_id": candidate.id,
            "api_key": api_key,            # returned exactly once
            "nats_account": candidate.nats_account,
            "cert_issued": cert is not None,
        }
        if cert:
            body.update({
                "certificate": cert["certificate"],
                "private_key": cert["private_key"],
                "issuing_ca": cert["issuing_ca"],
            })
        logger.info("collector %s enrolled (cert_issued=%s)", candidate.id, cert is not None)
        return Response(body, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def heartbeat(self, request):
        """API-key-authenticated liveness beat from a remote collector."""
        collector = authenticate_collector(request)
        if collector is None:
            return Response({"error": "unauthenticated"}, status=status.HTTP_401_UNAUTHORIZED)
        collector.last_seen_at = timezone.now()
        if collector.status != Collector.Status.REVOKED:
            collector.status = Collector.Status.ACTIVE
        if request.data.get("version"):
            collector.version = str(request.data["version"])[:50]
        if isinstance(request.data.get("capabilities"), dict):
            collector.capabilities = request.data["capabilities"]
        collector.remote_ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR")
        )
        collector.save(update_fields=[
            "last_seen_at", "status", "version", "capabilities", "remote_ip", "updated_at",
        ])
        return Response({"status": collector.status, "ok": True})

    @action(detail=True, methods=["post"], url_path="regenerate-token")
    def regenerate_token(self, request, pk=None):
        """Issue a fresh one-time enrollment token (re-bootstrap). Admin only."""
        collector = self.get_object()
        token = auth.generate_enrollment_token()
        collector.enrollment_token_hash = auth.hash_secret(token)
        collector.enrolled_at = None
        collector.save(update_fields=["enrollment_token_hash", "enrolled_at", "updated_at"])
        return Response({"enrollment_token": token})

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Revoke a collector (blocks heartbeat/enroll; severs trust)."""
        collector = self.get_object()
        collector.status = Collector.Status.REVOKED
        collector.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(collector).data)

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List devices explicitly assigned to this collector."""
        collector = self.get_object()
        return Response(DeviceListSerializer(collector.devices.all(), many=True).data)
