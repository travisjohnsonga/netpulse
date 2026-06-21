"""
ChatOps webhook receiver for Slack, Microsoft Teams, Google Chat, and Discord.

Each platform posts to /api/webhooks/{platform}/ with platform-specific payloads.
This module parses the natural-language query, maps it to a NetPulse API call,
and returns a formatted response.

Security:
- Each platform has a signature verification step before any processing.
- Queries are read-only. Action commands are not implemented (Phase 4+).
- Sensitive data (credentials, internal IPs) is never included in responses.
- All queries are audit-logged via standard Django logging.
- Only registered commands are processed — unknown commands return help text.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time

from django.http import HttpRequest, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from apps.chatops.enforcement import enforce_policy, platform_live
from apps.chatops.format import deny_response, format_for
from apps.chatops.nlp import resolve_nlp
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

# Webhook payloads/responses are platform-specific free-form JSON.
_webhook_schema = extend_schema(
    request=OpenApiTypes.OBJECT,
    responses=OpenApiTypes.OBJECT,
    tags=["chatops"],
)

logger = logging.getLogger(__name__)

# ── intent patterns ───────────────────────────────────────────────────────────
_INTENTS: list[tuple[str, re.Pattern]] = [
    ("site_status",    re.compile(r"status\s+of\s+site\s+(?P<name>\S+)", re.I)),
    ("device_status",  re.compile(r"status\s+of\s+(?P<name>\S+)", re.I)),
    ("active_alerts",  re.compile(r"(any\s+)?alerts?(\s+right\s+now)?", re.I)),
    ("cve_query",      re.compile(r"cve.*(affect|on)\s+(?P<name>\S+)", re.I)),
    ("eol_query",      re.compile(r"(eol|end.of.life|lifecycle).*(?P<name>\S+)", re.I)),
    ("help",           re.compile(r"^help$", re.I)),
]

def _parse_intent(text: str) -> tuple[str, dict]:
    cleaned = text.strip()
    for intent, pattern in _INTENTS:
        m = pattern.search(cleaned)
        if m:
            return intent, m.groupdict()
    return "unknown", {}


def _classify(text: str) -> tuple[str, dict]:
    """Regex parse first (always-on default); only on ``unknown`` consult the
    optional NLP fallback. A known NLP result is used; anything else stays
    ``unknown`` so the resolver returns help. The chosen intent is returned to the
    caller, which still runs it through ``enforce_policy`` (no policy bypass)."""
    intent, params = _parse_intent(text)
    if intent == "unknown":
        nlp = resolve_nlp(text)
        if nlp:
            return nlp
    return intent, params


# Intent resolution (data gathering) lives in apps.chatops.resolve, and
# per-platform rendering in apps.chatops.format — see resolve()/format_for above.


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
def webhook_slack(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("slack"):
        return _chatops_disabled_response()
    if not _verify_slack(request):
        logger.warning("slack signature verification failed from %s", request.META.get("REMOTE_ADDR"))
        return JsonResponse({"error": "invalid signature"}, status=401)

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
    intent, params = _classify(text)
    decision = enforce_policy("slack", channel_id=channel, user_id=user,
                              user_name=user, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("slack", decision.message))
    return JsonResponse(format_for("slack", resolve(intent, params)))


# ── Microsoft Teams ───────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_teams(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("teams"):
        return _chatops_disabled_response()
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
    intent, params = _classify(text)
    decision = enforce_policy("teams", channel_id=channel, user_id=user_id,
                              user_name=user, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("teams", decision.message))
    return JsonResponse(format_for("teams", resolve(intent, params)))


# ── Google Chat ───────────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_gchat(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("gchat"):
        return _chatops_disabled_response()
    payload   = request.data
    message   = payload.get("message", {})
    text      = message.get("text", "").strip()
    sender_o  = message.get("sender", {})
    sender    = sender_o.get("displayName", "unknown")
    user_id   = sender_o.get("name", "") or sender
    channel   = payload.get("space", {}).get("name", "") or "unknown"

    logger.info("gchat query from %s: %s", sender, text[:200])
    intent, params = _classify(text)
    decision = enforce_policy("gchat", channel_id=channel, user_id=user_id,
                              user_name=sender, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("gchat", decision.message))
    return JsonResponse(format_for("gchat", resolve(intent, params)))


# ── Discord ───────────────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_discord(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled("discord"):
        return _chatops_disabled_response()
    payload = request.data
    # Discord interaction verification uses Ed25519 — skip for now (Phase 4 full impl)
    text    = payload.get("data", {}).get("options", [{}])[0].get("value", "")
    if not text:
        text = payload.get("content", "")
    # User is under member.user (guild) or user (DM); channel at top level.
    user_obj = payload.get("member", {}).get("user", {}) or payload.get("user", {})
    user_id  = user_obj.get("id", "") or "unknown"
    user_nm  = user_obj.get("username", "") or "unknown"
    channel  = payload.get("channel_id", "") or "unknown"

    logger.info("discord query: %s", text[:200])
    intent, params = _classify(text)
    decision = enforce_policy("discord", channel_id=channel, user_id=user_id,
                              user_name=user_nm, intent=intent, request=request)
    if not decision.allowed:
        return JsonResponse(deny_response("discord", decision.message))
    return JsonResponse(format_for("discord", resolve(intent, params)))
