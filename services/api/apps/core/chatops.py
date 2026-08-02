"""
ChatOps webhook receiver for Slack, Microsoft Teams, Google Chat, Discord, and
Mattermost.

Each platform posts to /api/webhooks/{platform}/ with platform-specific payloads.
This module parses the natural-language query, maps it to a NetPulse API call,
and returns a formatted response.

Security:
- EVERY platform authenticates the request before any processing (identity is
  read from the payload and fed to enforce_policy, so an unauthenticated webhook
  would be identity-spoofable). Each platform's verifier fails closed and is only
  skipped when no secret is configured (dev mode). See the _verify_* helpers.
- All five webhooks are rate-limited (the "chatops" throttle scope) to blunt
  brute-force/abuse and NLP-call amplification.
- Queries are read-only. Action commands are not implemented (Phase 4+).
- Sensitive data (credentials, internal IPs) is never included in responses.
- All queries are audit-logged via standard Django logging.
- Signatures, tokens, and keys are NEVER logged.
- Only registered commands are processed — unknown commands return help text.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import time

from django.http import HttpRequest, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny

from apps.chatops.enforcement import enforce_policy, platform_live
from apps.core.client_ip import TrustedProxySimpleRateThrottle
from apps.chatops.format import deny_response, format_for
from apps.chatops.pipeline import classify
from apps.chatops.resolve import resolve


def _chatops_enabled(platform: str) -> bool:
    """ChatOps inbound webhooks are AllowAny (the platforms can't send a JWT) and
    Teams/Google Chat/Discord have no signature step, so an enabled webhook is an
    unauthenticated read into inventory/alert data. A webhook is live only when
    the ``CHATOPS_ENABLED`` master kill-switch is on AND that platform's
    ``ChatOpsPlatform`` row is enabled (see apps.chatops.enforcement). A disabled
    platform returns 404 — not revealing the route exists — before any parsing."""
    return platform_live(platform)


def _chatops_disabled_response() -> JsonResponse:
    return JsonResponse({"error": "not found"}, status=404)


class WebhookThrottle(TrustedProxySimpleRateThrottle):
    """Per-client rate limit on the inbound webhook endpoints (scope "chatops").

    Blunts brute-force of the signature/token checks below and caps NLP-call
    amplification from a flood of unknown-intent queries. Keyed on the real,
    spoof-resistant client IP (shared get_client_ip / NUM_PROXIES — counted from
    the right of X-Forwarded-For). The rate comes from settings
    DEFAULT_THROTTLE_RATES["chatops"]; with the scope fixed on the class (rather
    than read from a view's throttle_scope, which the function-based webhook views
    don't set) it applies uniformly across all five endpoints."""
    scope = "chatops"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def _unauthorized(platform: str, request: HttpRequest) -> JsonResponse:
    """401 for a failed webhook verification. Never logs the signature/token."""
    logger.warning("%s webhook verification failed from %s",
                   platform, request.META.get("REMOTE_ADDR"))
    return JsonResponse({"error": "invalid signature"}, status=401)

# Webhook payloads/responses are platform-specific free-form JSON.
_webhook_schema = extend_schema(
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
    tags=["chatops"],
)

logger = logging.getLogger(__name__)

# NL classification (regex parse → NLP fallback) lives in apps.chatops.pipeline
# (classify) so the authenticated in-UI query endpoint shares it; intent
# resolution (data gathering) lives in apps.chatops.resolve, and per-platform
# rendering in apps.chatops.format — see classify()/resolve()/format_for above.

# Teams times out an outgoing webhook after 5s, so the NLP fallback gets a tight
# budget here (leaving room for resolve + render); the other surfaces use the
# full CHATOPS_NLP_TIMEOUT_S. Passed to classify(nlp_budget=…) below.
TEAMS_NLP_BUDGET_S = 3


# ── Slack ─────────────────────────────────────────────────────────────────────

def _verify_slack(request: HttpRequest) -> bool:
    from apps.chatops.models import get_chatops_secret
    secret = get_chatops_secret("slack", "signing_secret")
    if not secret:
        return True   # Skip verification if not configured (dev mode)
    ts   = request.headers.get("X-Slack-Request-Timestamp", "")
    sig  = request.headers.get("X-Slack-Signature", "")
    if abs(time.time() - float(ts or "0")) > 300:
        return False
    base = f"v0:{ts}:{request.body.decode()}"
    expected = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([WebhookThrottle])
def webhook_slack(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("slack"):
        return _chatops_disabled_response()
    if not _verify_slack(request):
        return _unauthorized("slack", request)

    payload = request.data
    # Handle Slack URL verification challenge
    if payload.get("type") == "url_verification":
        return JsonResponse({"challenge": payload.get("challenge", "")})

    event = payload.get("event", {})
    text  = event.get("text", "").strip()
    # Strip bot mention: "<@BOTID> status of ..."
    text  = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    user  = event.get("user", "unknown")
    channel = event.get("channel", "unknown")

    logger.info("slack query from %s in %s: %s", user, channel, text[:200])
    intent, params = classify(text)
    decision = enforce_policy("slack", channel_id=channel, user_id=user,
                              user_name=user, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("slack", decision.message))
    return JsonResponse(format_for("slack", resolve(intent, params)))


# ── Microsoft Teams ───────────────────────────────────────────────────────────

def _verify_teams(request: HttpRequest) -> bool:
    """Verify a Teams *outgoing-webhook* HMAC.

    Teams signs the raw request body with HMAC-SHA256 using the base64 security
    token issued when the outgoing webhook is registered, and sends it as
    ``Authorization: HMAC <base64-sig>``. We base64-decode the stored token to
    the raw key, recompute the HMAC over ``request.body`` (raw bytes — the parsed
    body re-serializes differently), and compare constant-time.

    Alternative model: a fully-registered Teams *bot* authenticates with a Bot
    Framework JWT (``Authorization: Bearer <jwt>`` issued by
    ``https://api.botframework.com``) instead — out of scope here; this verifies
    the simpler outgoing-webhook HMAC. Skipped (dev mode) when no secret is set."""
    from apps.chatops.models import get_chatops_secret
    secret = get_chatops_secret("teams", "hmac_secret")
    if not secret:
        return True   # dev mode: no shared secret configured
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("HMAC "):
        return False
    provided = auth[len("HMAC "):].strip()
    try:
        key = base64.b64decode(secret)
    except ValueError:   # binascii.Error (malformed base64) subclasses ValueError
        return False
    digest = hmac.new(key, request.body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, provided)


@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([WebhookThrottle])
def webhook_teams(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("teams"):
        return _chatops_disabled_response()
    if not _verify_teams(request):
        return _unauthorized("teams", request)
    # Teams sends an HMAC in Authorization header if outgoing webhook HMAC is configured
    payload  = request.data
    text     = payload.get("text", "")
    # Teams wraps text in HTML — strip tags. Possessive `[^>]++` (Python 3.11+)
    # keeps this linear on untrusted webhook input (avoids polynomial ReDoS).
    text     = re.sub(r"<[^>]++>", "", text).strip()
    from_obj = payload.get("from", {})
    user     = from_obj.get("name", "unknown")
    user_id  = from_obj.get("id", "") or user
    channel  = payload.get("conversation", {}).get("id", "") or "unknown"

    logger.info("teams query from %s: %s", user, text[:200])
    # Cap the NLP wait so a fallback stays inside Teams' 5s response window.
    intent, params = classify(text, nlp_budget=TEAMS_NLP_BUDGET_S)
    decision = enforce_policy("teams", channel_id=channel, user_id=user_id,
                              user_name=user, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("teams", decision.message))
    return JsonResponse(format_for("teams", resolve(intent, params)))


# ── Google Chat ───────────────────────────────────────────────────────────────

def _verify_gchat(request: HttpRequest) -> bool:
    """Authenticate a Google Chat request via a shared bearer token.

    Interim model: compare the ``Authorization: Bearer <token>`` value
    constant-time against the stored ``bearer_token``. Skipped (dev mode) when no
    token is configured.

    TODO (full Google-signed JWT validation): Google Chat actually sends a Bearer
    JWT signed by Google whose ``iss`` is ``chat@system.gserviceaccount.com`` and
    whose ``aud`` is the configured project number; it should be verified against
    Google's public certs (https://www.googleapis.com/service_accounts/v1/metadata/x509/chat@system.gserviceaccount.com).
    That requires the ``google-auth`` dependency (not currently installed); until
    then the shared-bearer compare above keeps the endpoint authenticated rather
    than open."""
    from apps.chatops.models import get_chatops_secret
    secret = get_chatops_secret("gchat", "bearer_token")
    if not secret:
        return True   # dev mode: no shared bearer configured
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    provided = auth[len("Bearer "):].strip()
    return hmac.compare_digest(provided, secret)


@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([WebhookThrottle])
def webhook_gchat(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("gchat"):
        return _chatops_disabled_response()
    if not _verify_gchat(request):
        return _unauthorized("gchat", request)
    payload   = request.data
    message   = payload.get("message", {})
    text      = message.get("text", "").strip()
    sender_o  = message.get("sender", {})
    sender    = sender_o.get("displayName", "unknown")
    user_id   = sender_o.get("name", "") or sender
    channel   = payload.get("space", {}).get("name", "") or "unknown"

    logger.info("gchat query from %s: %s", sender, text[:200])
    intent, params = classify(text)
    decision = enforce_policy("gchat", channel_id=channel, user_id=user_id,
                              user_name=sender, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("gchat", decision.message))
    return JsonResponse(format_for("gchat", resolve(intent, params)))


# ── Discord ───────────────────────────────────────────────────────────────────

def _verify_discord(request: HttpRequest) -> bool:
    """Verify a Discord interaction's Ed25519 signature.

    Discord signs ``X-Signature-Timestamp + raw-body`` with the application's
    private key; we verify against the provisioned ``public_key`` using PyNaCl.
    The raw ``request.body`` is required (re-serializing the parsed body changes
    the bytes and breaks the signature). Skipped (dev mode) when no public key is
    configured."""
    from apps.chatops.models import get_chatops_secret
    public_key = get_chatops_secret("discord", "public_key")
    if not public_key:
        return True   # dev mode: no public key configured
    sig = request.headers.get("X-Signature-Ed25519", "")
    ts  = request.headers.get("X-Signature-Timestamp", "")
    if not sig or not ts:
        return False
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(ts.encode() + request.body, bytes.fromhex(sig))
        return True
    except (BadSignatureError, ValueError):
        # Bad signature, or malformed hex in the header/key.
        return False


@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([WebhookThrottle])
def webhook_discord(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("discord"):
        return _chatops_disabled_response()
    if not _verify_discord(request):
        return _unauthorized("discord", request)
    payload = request.data
    # Discord PING (interaction type 1) — Discord's endpoint validation handshake;
    # answer with a type-1 PONG ACK. Only honoured AFTER signature verification.
    if payload.get("type") == 1:
        return JsonResponse({"type": 1})
    text    = payload.get("data", {}).get("options", [{}])[0].get("value", "")
    if not text:
        text = payload.get("content", "")
    # User is under member.user (guild) or user (DM); channel at top level.
    user_obj = payload.get("member", {}).get("user", {}) or payload.get("user", {})
    user_id  = user_obj.get("id", "") or "unknown"
    user_nm  = user_obj.get("username", "") or "unknown"
    channel  = payload.get("channel_id", "") or "unknown"

    logger.info("discord query: %s", text[:200])
    intent, params = classify(text)
    decision = enforce_policy("discord", channel_id=channel, user_id=user_id,
                              user_name=user_nm, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("discord", decision.message))
    return JsonResponse(format_for("discord", resolve(intent, params)))


# ── Mattermost ──────────────────────────────────────────────────────────────────

def _verify_mattermost(request: HttpRequest) -> bool:
    """Verify a Mattermost outgoing webhook by its shared token.

    Mattermost includes the per-webhook ``token`` it was configured with in each
    POST; compare it constant-time against the stored token. Skipped (dev mode)
    when no token is configured."""
    from apps.chatops.models import get_chatops_secret
    secret = get_chatops_secret("mattermost", "token")
    if not secret:
        return True   # dev mode: no token configured
    provided = request.data.get("token", "") or ""
    return hmac.compare_digest(str(provided), secret)


@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
@authentication_classes([])
@throttle_classes([WebhookThrottle])
def webhook_mattermost(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("mattermost"):
        return _chatops_disabled_response()
    if not _verify_mattermost(request):
        return _unauthorized("mattermost", request)
    # Mattermost outgoing webhooks POST form-encoded fields (user_name, text, …).
    payload  = request.data
    text     = (payload.get("text", "") or "").strip()
    # Strip a leading trigger word / bot mention if present ("@spane status of …").
    text     = re.sub(r"^@?\S+\s+", "", text) if text.startswith("@") else text
    user     = payload.get("user_name", "") or "unknown"
    user_id  = payload.get("user_id", "") or user
    channel  = payload.get("channel_id", "") or "unknown"

    logger.info("mattermost query from %s: %s", user, text[:200])
    intent, params = classify(text)
    decision = enforce_policy("mattermost", channel_id=channel, user_id=user_id,
                              user_name=user, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("mattermost", decision.message))
    return JsonResponse(format_for("mattermost", resolve(intent, params)))
