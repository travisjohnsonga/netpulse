"""
ChatOps configuration API (Settings → ChatOps).

- ``platforms``  — per-platform enable + secret management (admin writes).
- ``channels``   — approved-channel allow-list CRUD (admin writes).
- ``identities`` — chat-user → spane-user mapping (admin CRUD) + a self-service
  ``link/`` claim any authenticated user can call.
- ``config``     — singleton global policy flags (admin writes).

Secrets are write-only and never returned (Security Rules 3 + 4).
"""
from __future__ import annotations

import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import CapabilityViewSetMixin, HasCapability

from .enforcement import enforce_policy
from .models import (
    ChatOpsChannel, ChatOpsConfig, ChatOpsIdentity, ChatOpsPlatform,
    PLATFORM_SECRET_FIELDS, read_chatops_secrets,
)
from .pipeline import classify
from .resolve import resolve
from .serializers import (
    ChatOpsChannelSerializer, ChatOpsConfigSerializer,
    ChatOpsIdentityLinkSerializer, ChatOpsIdentitySerializer,
    ChatOpsPlatformSerializer,
)

logger = logging.getLogger(__name__)

_VALID_PLATFORMS = {c[0] for c in ChatOpsPlatform.Platform.choices}


class ChatOpsPlatformViewSet(CapabilityViewSetMixin, viewsets.ViewSet):
    """Per-platform ChatOps config (one row per platform), keyed by platform slug."""

    view_capability = "chatops:use"
    write_capability = "chatops:manage"

    def _load(self, platform: str) -> ChatOpsPlatform | None:
        if platform not in _VALID_PLATFORMS:
            return None
        obj, _ = ChatOpsPlatform.objects.get_or_create(platform=platform)
        return obj

    @extend_schema(responses=ChatOpsPlatformSerializer(many=True))
    def list(self, request):
        # Surface a row for every platform so the UI can render all of them.
        rows = [self._load(p) for p in sorted(_VALID_PLATFORMS)]
        return Response(ChatOpsPlatformSerializer(rows, many=True).data)

    @extend_schema(responses=ChatOpsPlatformSerializer)
    def retrieve(self, request, platform=None):
        obj = self._load(platform)
        if obj is None:
            return Response({"error": "unknown platform"}, status=status.HTTP_404_NOT_FOUND)
        return Response(ChatOpsPlatformSerializer(obj).data)

    @extend_schema(request=ChatOpsPlatformSerializer, responses=ChatOpsPlatformSerializer)
    def update(self, request, platform=None):
        obj = self._load(platform)
        if obj is None:
            return Response({"error": "unknown platform"}, status=status.HTTP_404_NOT_FOUND)
        ser = ChatOpsPlatformSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ChatOpsPlatformSerializer(self._load(platform)).data)

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def test(self, request, platform=None):
        """Verify the platform's stored credentials are present/usable.

        Without bundled per-platform SDKs this is a stored-credential check: it
        confirms the secret(s) the platform needs are present in OpenBao (never
        returning their values). When a future platform client is wired in it can
        replace this with a live reachability call.
        """
        if platform not in _VALID_PLATFORMS:
            return Response({"error": "unknown platform"}, status=status.HTTP_404_NOT_FOUND)
        required = PLATFORM_SECRET_FIELDS.get(platform, ())
        stored = read_chatops_secrets(platform)
        missing = [f for f in required if not stored.get(f)]
        if missing:
            return Response(
                {"connected": False,
                 "message": f"Missing stored credential(s): {', '.join(missing)}."},
                status=status.HTTP_400_BAD_REQUEST)
        return Response({"connected": True,
                         "message": "Stored credentials present."})


class ChatOpsChannelViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Approved-channel allow-list CRUD (admin writes)."""

    view_capability = "chatops:use"
    write_capability = "chatops:manage"
    queryset = ChatOpsChannel.objects.all()
    serializer_class = ChatOpsChannelSerializer
    filterset_fields = ["platform", "enabled", "purpose"]


class ChatOpsIdentityViewSet(viewsets.ModelViewSet):
    """Chat-user → spane-user identity mapping (admin CRUD) + self-service link."""

    queryset = ChatOpsIdentity.objects.select_related("user").all()
    serializer_class = ChatOpsIdentitySerializer
    filterset_fields = ["platform", "user"]

    def get_permissions(self):
        if self.action == "link":
            return [IsAuthenticated()]
        return [HasCapability("chatops:manage")()]

    @extend_schema(request=ChatOpsIdentityLinkSerializer, responses=ChatOpsIdentitySerializer)
    @action(detail=False, methods=["post"])
    def link(self, request):
        """Claim a (platform, platform_user_id) for the authenticated spane user.

        Any authenticated user may link their OWN chat identity. A pair already
        claimed by another user is rejected; an unclaimed/own pair is (re)linked.
        """
        ser = ChatOpsIdentityLinkSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        platform = ser.validated_data["platform"]
        uid = ser.validated_data["platform_user_id"]
        name = ser.validated_data.get("platform_user_name", "")

        existing = ChatOpsIdentity.objects.filter(platform=platform, platform_user_id=uid).first()
        if existing and existing.user_id and existing.user_id != request.user.id:
            return Response(
                {"error": "This chat identity is already linked to another user."},
                status=status.HTTP_409_CONFLICT)
        if existing:
            existing.user = request.user
            if name:
                existing.platform_user_name = name
            existing.save()
            identity = existing
        else:
            identity = ChatOpsIdentity.objects.create(
                platform=platform, platform_user_id=uid,
                platform_user_name=name, user=request.user)
        return Response(ChatOpsIdentitySerializer(identity).data, status=status.HTTP_200_OK)


class ChatOpsConfigView(APIView):
    """GET / PUT the singleton global ChatOps policy."""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("chatops:use")()]
        return [HasCapability("chatops:manage")()]

    @extend_schema(responses=ChatOpsConfigSerializer)
    def get(self, request):
        return Response(ChatOpsConfigSerializer(ChatOpsConfig.load()).data)

    @extend_schema(request=ChatOpsConfigSerializer, responses=ChatOpsConfigSerializer)
    def put(self, request):
        ser = ChatOpsConfigSerializer(ChatOpsConfig.load(), data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ChatOpsConfigSerializer(ChatOpsConfig.load()).data)


class ChatOpsQueryView(APIView):
    """Authenticated in-UI ChatOps query — the slide-out chat panel's backend.

    Runs the SAME classify → enforce_policy → resolve pipeline the platform
    webhooks use, but with the logged-in user as the resolved identity: no
    signature, no ChatOpsIdentity mapping — a first-party authenticated call.

    Deliberately NOT gated behind ``CHATOPS_ENABLED``. That master switch governs
    the inbound *webhook* endpoints, which are AllowAny (the platforms can't send a
    JWT) and therefore unauthenticated reads into inventory/alert data the kill
    switch must be able to shut off. This in-UI surface requires a logged-in
    session and audits every query, so it has none of those risks and works
    regardless of the webhook kill switch.
    """

    permission_classes = [HasCapability("chatops:use")]

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT,
                   tags=["chatops"], summary="Run an authenticated in-UI ChatOps query")
    def post(self, request):
        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "Enter a question to ask spane."},
                            status=status.HTTP_400_BAD_REQUEST)

        intent, params = classify(text)
        decision = enforce_policy(
            "web",
            channel_id=f"web:{request.user.username}",
            user_id=str(request.user.pk),
            user_name=request.user.username,
            intent=intent,
            request=request,
            authenticated_user=request.user,
        )
        if not decision.allowed:
            return Response({"denied": True, "message": decision.message})

        return Response(resolve(intent, params).to_dict())
