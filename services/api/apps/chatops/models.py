"""
ChatOps persistence + configuration.

Engineers query spane from chat platforms (Slack/Teams/Google Chat/Discord/
Mattermost) via the webhook receivers in ``apps.core.chatops``. This app holds
the DB-backed configuration those receivers consult:

- ``ChatOpsPlatform`` — one row per platform: the per-platform enable flag and
  non-secret display settings. Platform secrets (Slack signing secret, bot
  tokens, Discord public key, Mattermost token) are **never** model fields —
  they live in OpenBao at ``spane/chatops/{platform}`` (see
  ``CHATOPS_VAULT_PREFIX``), mirroring the SMTP password pattern in
  ``apps.integrations.email``. We use the ``spane/*`` OpenBao namespace
  (following ``apps.backup``'s ``spane/backup/*`` precedent), not the legacy
  ``netpulse/*`` paths.
- ``ChatOpsChannel`` — the approved-channel allow-list (Phase 2 enforcement).
- ``ChatOpsIdentity`` — maps a chat user to a NetPulseUser for RBAC (Phase 2).
- ``ChatOpsConfig`` — singleton global ChatOps policy flags (Phase 2).
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel

logger = logging.getLogger(__name__)

# OpenBao namespace for ChatOps secrets. Each platform's secret bundle lives at
# ``spane/chatops/{platform}`` (e.g. spane/chatops/slack). Never the DB.
CHATOPS_VAULT_PREFIX = "spane/chatops"

# The write-only secret fields each platform stores in OpenBao. The serializer
# uses this to expose write-only inputs and a "stored" indicator; the values are
# never returned in any API response (Security Rules 3 + 4).
PLATFORM_SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "slack":      ("signing_secret", "bot_token"),
    "teams":      ("bot_token",),
    "gchat":      ("bot_token",),
    "discord":    ("public_key", "bot_token"),
    "mattermost": ("token",),
}


def chatops_vault_path(platform: str) -> str:
    """OpenBao path holding ``platform``'s secret bundle."""
    return f"{CHATOPS_VAULT_PREFIX}/{platform}"


def read_chatops_secrets(platform: str) -> dict:
    """Return the stored secret dict for ``platform`` ({} if unset/unavailable)."""
    from apps.credentials import vault
    try:
        return vault.read_secret(chatops_vault_path(platform)) or {}
    except Exception as exc:  # noqa: BLE001 — never raise from a webhook/secret path
        logger.warning("could not read ChatOps secrets for %s: %s", platform, exc)
        return {}


def get_chatops_secret(platform: str, key: str) -> str:
    """Read a single secret value for ``platform`` ('' if unset/unavailable)."""
    return read_chatops_secrets(platform).get(key, "") or ""


def write_chatops_secrets(platform: str, secrets: dict) -> None:
    """Write the supplied (non-blank) secret fields for ``platform`` to OpenBao.

    Blank/None values are dropped by ``vault.write_secret`` so saving non-secret
    settings (or only one of several secrets) never wipes the others.
    """
    from apps.credentials import vault
    vault.write_secret(chatops_vault_path(platform), secrets)


class ChatOpsPlatform(TimestampedModel):
    """
    Per-platform ChatOps configuration (one row per platform). The per-platform
    ``enabled`` flag gates that platform's inbound webhook; combined with the
    ``CHATOPS_ENABLED`` master kill-switch, a webhook is live only when BOTH are
    on. Secrets are stored in OpenBao at ``spane/chatops/{platform}`` — never on
    this row.
    """
    class Platform(models.TextChoices):
        SLACK = "slack", "Slack"
        TEAMS = "teams", "Microsoft Teams"
        GCHAT = "gchat", "Google Chat"
        DISCORD = "discord", "Discord"
        MATTERMOST = "mattermost", "Mattermost"

    platform = models.CharField(
        max_length=20, choices=Platform.choices, unique=True,
        help_text="Chat platform this row configures (one row per platform).",
    )
    enabled = models.BooleanField(default=False)
    display_name = models.CharField(max_length=128, blank=True)
    default_response_channel = models.CharField(
        max_length=128, blank=True,
        help_text="Channel id for proactive/notification responses (optional).",
    )

    class Meta(TimestampedModel.Meta):
        verbose_name = "ChatOps Platform"
        verbose_name_plural = "ChatOps Platforms"
        ordering = ["platform"]

    def __str__(self):
        return f"ChatOpsPlatform({self.platform}, {'enabled' if self.enabled else 'disabled'})"

    @property
    def vault_path(self) -> str:
        return chatops_vault_path(self.platform)

    @property
    def secret_fields(self) -> tuple[str, ...]:
        return PLATFORM_SECRET_FIELDS.get(self.platform, ())


class ChatOpsChannel(TimestampedModel):
    """
    Approved-channel allow-list. When ``ChatOpsConfig.require_approved_channel``
    is set, a query is only answered from an enabled channel whose ``purpose``
    permits queries (``query`` or ``both``). Enforcement lives in Phase 2.
    """
    class Purpose(models.TextChoices):
        QUERY = "query", "Query only"
        NOTIFY = "notify", "Notifications only"
        BOTH = "both", "Query + Notifications"

    platform = models.CharField(max_length=20, choices=ChatOpsPlatform.Platform.choices)
    channel_id = models.CharField(max_length=128)
    name = models.CharField(max_length=128, blank=True)
    purpose = models.CharField(max_length=10, choices=Purpose.choices, default=Purpose.BOTH)
    enabled = models.BooleanField(default=True)

    class Meta(TimestampedModel.Meta):
        verbose_name = "ChatOps Channel"
        verbose_name_plural = "ChatOps Channels"
        unique_together = [("platform", "channel_id")]
        ordering = ["platform", "name"]

    def __str__(self):
        return f"{self.platform}:{self.name or self.channel_id} ({self.purpose})"

    def allows_query(self) -> bool:
        return self.enabled and self.purpose in (self.Purpose.QUERY, self.Purpose.BOTH)


# ── Phase 2: identity mapping + global policy ─────────────────────────────────

class ChatOpsIdentity(TimestampedModel):
    """
    Maps a chat-platform user to a NetPulseUser so the mapped user's RBAC role
    governs what the chat user may query/do. Created by an admin or self-service
    claim (``POST /api/chatops/identities/link/``). ``user`` is nullable so an
    identity can be recorded (e.g. seen in chat) before it is linked.
    """
    platform = models.CharField(max_length=20, choices=ChatOpsPlatform.Platform.choices)
    platform_user_id = models.CharField(
        max_length=128, help_text="Stable per-platform user id (e.g. Slack U…).")
    platform_user_name = models.CharField(max_length=128, blank=True)
    user = models.ForeignKey(
        get_user_model(), null=True, blank=True, on_delete=models.CASCADE,
        related_name="chatops_identities",
    )

    class Meta(TimestampedModel.Meta):
        verbose_name = "ChatOps Identity"
        verbose_name_plural = "ChatOps Identities"
        unique_together = [("platform", "platform_user_id")]
        ordering = ["platform", "platform_user_name"]

    def __str__(self):
        who = self.user.username if self.user_id else "unmapped"
        return f"{self.platform}:{self.platform_user_name or self.platform_user_id} → {who}"


class ChatOpsConfig(TimestampedModel):
    """
    Singleton global ChatOps policy. ``allow_unmapped_read`` lets chat users with
    no linked account run read-only queries; ``require_approved_channel`` limits
    queries to channels in the ``ChatOpsChannel`` allow-list. Use ``load()`` to
    fetch the single row (mirrors EmailSettings).
    """
    allow_unmapped_read = models.BooleanField(
        default=True,
        help_text="Allow read-only queries from chat users with no linked spane account.",
    )
    require_approved_channel = models.BooleanField(
        default=False,
        help_text="Only answer queries from channels on the approved allow-list.",
    )

    class Meta:
        verbose_name = "ChatOps Config"
        verbose_name_plural = "ChatOps Config"

    def __str__(self):
        return "ChatOpsConfig"

    @classmethod
    def load(cls) -> "ChatOpsConfig":
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj
