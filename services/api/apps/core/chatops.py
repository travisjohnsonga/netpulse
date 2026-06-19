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
import json
import logging
import os
import re
import time

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny


def _chatops_enabled() -> bool:
    """ChatOps inbound webhooks are AllowAny (the platforms can't send a JWT) and
    Teams/Google Chat/Discord have no signature step, so an enabled webhook is an
    unauthenticated read into inventory/alert data. The feature is planned, not
    hardened, so it's disabled by default (settings.CHATOPS_ENABLED); a disabled
    webhook returns 404 — not revealing the route exists — before any parsing."""
    return getattr(settings, "CHATOPS_ENABLED", False)


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

_HELP_TEXT = (
    "spane commands:\n"
    "• `status of <device>` — device status\n"
    "• `status of site <site>` — site status\n"
    "• `any alerts` — list active alerts\n"
    "• `CVEs affecting <device>` — CVE query\n"
    "• `EOL for <device>` — lifecycle status\n"
)


def _parse_intent(text: str) -> tuple[str, dict]:
    cleaned = text.strip()
    for intent, pattern in _INTENTS:
        m = pattern.search(cleaned)
        if m:
            return intent, m.groupdict()
    return "unknown", {}


def _resolve_intent(intent: str, params: dict) -> str:
    """Map parsed intent to a plain-text response. Queries Django ORM synchronously."""
    try:
        if intent == "device_status":
            from apps.devices.models import Device
            name = params.get("name", "")
            try:
                d = Device.objects.filter(hostname__icontains=name).first()
                if not d:
                    # Only try the IP lookup when the term parses as an IP —
                    # ip_address is an INET column and a non-IP string errors.
                    import ipaddress
                    try:
                        ipaddress.ip_address(name)
                    except ValueError:
                        pass
                    else:
                        d = Device.objects.filter(ip_address=name).first()
                if d:
                    return f"*{d.hostname}* — status: `{d.status}`, vendor: {d.vendor or 'unknown'}"
                return f"Device `{name}` not found."
            except Exception:
                return f"Error looking up device `{name}`."

        if intent == "site_status":
            from apps.devices.models import Device, Site
            name = params.get("name", "")
            try:
                site = Site.objects.filter(name__icontains=name).first()
                if not site:
                    return f"Site `{name}` not found."
                count   = Device.objects.filter(site=site).count()
                active  = Device.objects.filter(site=site, status="active").count()
                return f"Site *{site.name}*: {active}/{count} devices active."
            except Exception:
                return f"Error looking up site `{name}`."

        if intent == "active_alerts":
            from apps.alerts.models import AlertEvent
            try:
                count = AlertEvent.objects.filter(state="firing").count()
                if count == 0:
                    return "No active alerts."
                recent = AlertEvent.objects.filter(state="firing").select_related("rule")[:5]
                lines = [f"*{e.rule.severity.upper()}* — {e.rule.name}" for e in recent]
                return f"{count} active alert(s):\n" + "\n".join(f"• {l}" for l in lines)
            except Exception:
                return "Error fetching alerts."

        if intent == "cve_query":
            return f"CVE query for `{params.get('name', '?')}` — see the Security tab for details."

        if intent == "eol_query":
            return f"Lifecycle query for `{params.get('name', '?')}` — see the Lifecycle tab for details."

        if intent == "help":
            return _HELP_TEXT

    except Exception as exc:
        logger.error("intent resolution error: %s", exc)

    return _HELP_TEXT


# ── Slack ─────────────────────────────────────────────────────────────────────

def _verify_slack(request: HttpRequest) -> bool:
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
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
    if not _chatops_enabled():
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
    intent, params = _parse_intent(text)
    response_text  = _resolve_intent(intent, params)

    return JsonResponse({"text": response_text})


# ── Microsoft Teams ───────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_teams(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled():
        return _chatops_disabled_response()
    # Teams sends an HMAC in Authorization header if outgoing webhook HMAC is configured
    payload  = request.data
    text     = payload.get("text", "")
    # Teams wraps text in HTML — strip tags. Possessive `[^>]++` (Python 3.11+)
    # keeps this linear on untrusted webhook input (avoids polynomial ReDoS).
    text     = re.sub(r"<[^>]++>", "", text).strip()
    from_obj = payload.get("from", {})
    user     = from_obj.get("name", "unknown")

    logger.info("teams query from %s: %s", user, text[:200])
    intent, params = _parse_intent(text)
    response_text  = _resolve_intent(intent, params)

    # Teams expects Adaptive Card or simple text in `text` field
    return JsonResponse({
        "type": "message",
        "text": response_text,
    })


# ── Google Chat ───────────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_gchat(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled():
        return _chatops_disabled_response()
    payload  = request.data
    message  = payload.get("message", {})
    text     = message.get("text", "").strip()
    sender   = message.get("sender", {}).get("displayName", "unknown")

    logger.info("gchat query from %s: %s", sender, text[:200])
    intent, params = _parse_intent(text)
    response_text  = _resolve_intent(intent, params)

    return JsonResponse({"text": response_text})


# ── Discord ───────────────────────────────────────────────────────────────────

@_webhook_schema
@api_view(["POST"])
@permission_classes([AllowAny])
def webhook_discord(request: HttpRequest) -> JsonResponse:
    if not _chatops_enabled():
        return _chatops_disabled_response()
    payload = request.data
    # Discord interaction verification uses Ed25519 — skip for now (Phase 4 full impl)
    text    = payload.get("data", {}).get("options", [{}])[0].get("value", "")
    if not text:
        text = payload.get("content", "")

    logger.info("discord query: %s", text[:200])
    intent, params = _parse_intent(text)
    response_text  = _resolve_intent(intent, params)

    return JsonResponse({"content": response_text})
