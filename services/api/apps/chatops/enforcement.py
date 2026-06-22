"""
ChatOps gating + policy enforcement shared by the webhook receivers in
``apps.core.chatops``.

Two layers:

- **Phase 1 gating** (``platform_live``): a platform's inbound webhook is live
  only when the ``CHATOPS_ENABLED`` master kill-switch is on AND that platform's
  ``ChatOpsPlatform`` row is enabled. A dead platform returns 404 (handled by the
  caller) so the route isn't revealed.
- **Phase 2 policy** (``enforce_policy``): runs BEFORE intent resolution —
  approved-channel allow-list, chat-user → NetPulseUser identity mapping with the
  unmapped-read flag, and an audit row for every query (allowed or denied). No
  secret or raw token is ever logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# Polite, non-revealing denial messages (no internal detail).
CHANNEL_NOT_AUTHORIZED = (
    "This channel isn't authorized for spane queries. Ask an admin to add it to "
    "the approved channels list."
)
LINK_ACCOUNT = (
    "Your chat account isn't linked to a spane user yet. Link it under your spane "
    "profile (Settings → ChatOps) to run queries."
)

# Intents that perform actions/changes rather than read-only queries. ChatOps is
# read-only today (action commands are Phase 4+), so this stays empty; unmapped
# users are allowed read-only intents only, and every current intent qualifies.
_ACTION_INTENTS: frozenset[str] = frozenset()


def is_action_intent(intent: str) -> bool:
    return intent in _ACTION_INTENTS


def master_enabled() -> bool:
    """The ``CHATOPS_ENABLED`` master kill-switch."""
    return bool(getattr(settings, "CHATOPS_ENABLED", False))


def platform_live(platform: str) -> bool:
    """True only when the master switch is on AND ``platform``'s row is enabled."""
    if not master_enabled():
        return False
    from .models import ChatOpsPlatform
    return ChatOpsPlatform.objects.filter(platform=platform, enabled=True).exists()


def resolve_identity(platform: str, platform_user_id: str):
    """Return the ChatOpsIdentity for ``(platform, user)`` or None."""
    if not platform_user_id:
        return None
    from .models import ChatOpsIdentity
    return (
        ChatOpsIdentity.objects
        .select_related("user")
        .filter(platform=platform, platform_user_id=platform_user_id)
        .first()
    )


def channel_allows_query(platform: str, channel_id: str) -> bool:
    """True if ``channel_id`` is an enabled query/both channel for ``platform``."""
    if not channel_id:
        return False
    from .models import ChatOpsChannel
    ch = ChatOpsChannel.objects.filter(platform=platform, channel_id=channel_id).first()
    return bool(ch and ch.allows_query())


@dataclass
class Decision:
    allowed: bool
    message: str = ""          # populated only when denied (sent back to chat)
    user = None                # resolved NetPulseUser (or None)
    role: str | None = None    # resolved RBAC role (or None when unmapped)


def enforce_policy(platform, *, channel_id, user_id, user_name, intent, request=None,
                   authenticated_user=None) -> Decision:
    """Apply approved-channel + identity policy and audit the query.

    Returns a :class:`Decision`; when ``allowed`` is False the caller replies with
    ``message``. Writes exactly one AuditLog row (``chatops_query`` when allowed,
    ``chatops_denied`` when denied) — never logging secrets/tokens.

    ``authenticated_user`` is the first-party in-UI path (the authenticated query
    endpoint): the resolved identity IS the logged-in user, not a payload-derived
    chat identity. Such a session has no approved channel and is inherently mapped,
    so the approved-channel and unmapped-user gates are skipped — but the query is
    STILL audited. All webhook call sites pass no ``authenticated_user`` and behave
    exactly as before.
    """
    from .models import ChatOpsConfig

    config = ChatOpsConfig.load()
    if authenticated_user is not None:
        user = authenticated_user
        role = getattr(user, "role", None)
        username = user.username
    else:
        identity = resolve_identity(platform, user_id)
        user = identity.user if (identity and identity.user_id) else None
        role = getattr(user, "role", None) if user else None
        username = user.username if user else "unmapped"

    def _audit(allowed: bool, reason: str = ""):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        event = AuditLog.EventType.CHATOPS_QUERY if allowed else AuditLog.EventType.CHATOPS_DENIED
        log_event(
            event, request=request, user=user, username=username,
            success=allowed,
            description=(f"ChatOps {platform} query by {username} in "
                        f"{channel_id or 'unknown'}: intent={intent}"
                        + (f" — denied ({reason})" if not allowed else "")),
            metadata={
                "platform": platform,
                "channel": channel_id or "",
                "chat_user": user_name or "",
                "username": username,
                "intent": intent,
                "allowed": allowed,
                "reason": reason,
            },
            error_message=reason if not allowed else "",
        )

    # The approved-channel and identity-mapping gates apply only to the webhook
    # path. A first-party authenticated session (authenticated_user set) has no
    # channel and is inherently mapped, so both are skipped — it still audits.
    if authenticated_user is None:
        # 1) Approved-channel allow-list.
        if config.require_approved_channel and not channel_allows_query(platform, channel_id):
            _audit(False, "channel_not_approved")
            return Decision(allowed=False, message=CHANNEL_NOT_AUTHORIZED)

        # 2) Identity mapping. Unmapped users may run read-only intents only when
        #    allow_unmapped_read; mapped users are governed by their RBAC role
        #    (read-only today, so any role may query).
        if user is None:
            if is_action_intent(intent) or not config.allow_unmapped_read:
                _audit(False, "unmapped_user")
                return Decision(allowed=False, message=LINK_ACCOUNT)

    decision = Decision(allowed=True)
    decision.user = user
    decision.role = role
    _audit(True)
    return decision
