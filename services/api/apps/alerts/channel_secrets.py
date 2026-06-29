"""
Per-channel secret handling for AlertChannels.

A channel's destination secret (a Teams/Slack/webhook incoming-webhook URL, a
PagerDuty routing key) is sensitive and must live in OpenBao, never PostgreSQL
or an API response (Security Rules #1–#4). This is the choke point:

- ``SECRET_KEYS`` lists the secret config keys per channel type.
- ``store_channel_secrets`` moves any provided secret keys out of ``config`` and
  into OpenBao at ``netpulse/alerts/channels/{id}``, leaving a non-secret
  ``{key}_set: true`` marker behind so the UI can show "configured".
- ``resolve_channel_secret`` reads a secret back (OpenBao first).

When OpenBao is **not** configured (dev / the test suite), secrets stay in
``config`` exactly as before — consistent with the rest of the vault layer,
which discards rather than persists when disabled.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Secret (OpenBao-bound) config keys per channel type.
SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "teams": ("webhook_url",),
    "slack": ("webhook_url",),
    "webhook": ("url", "webhook_url", "token"),
    "pagerduty": ("routing_key", "integration_key"),
    "email": (),
}


def secret_keys_for(channel_type: str) -> tuple[str, ...]:
    return SECRET_KEYS.get(channel_type, ())


def channel_vault_path(channel_id) -> str:
    return f"netpulse/alerts/channels/{channel_id}"


def store_channel_secrets(channel) -> None:
    """
    Persist any secret keys currently sitting in ``channel.config`` to OpenBao,
    replacing them with ``{key}_set`` markers. No-op (secrets left in config)
    when OpenBao is disabled. Saves the channel if config was mutated.
    """
    from apps.credentials import vault

    keys = secret_keys_for(channel.channel_type)
    if not keys or not isinstance(channel.config, dict):
        return
    present = {k: channel.config.get(k) for k in keys if channel.config.get(k)}
    if not present:
        return
    if not vault.vault_enabled():
        return  # dev/test: keep in config (vault would discard it)
    try:
        vault.write_secret(channel_vault_path(channel.pk), present)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not store secrets for channel %s: %s", channel.pk, exc)
        return
    cfg = dict(channel.config)
    for k in present:
        cfg.pop(k, None)
        cfg[f"{k}_set"] = True
    channel.config = cfg
    channel.save(update_fields=["config", "updated_at"])


def resolve_channel_secret(channel, key: str) -> str:
    """Return a channel secret value (OpenBao first, then config fallback)."""
    from apps.credentials import vault

    if vault.vault_enabled():
        try:
            secrets = vault.read_secret(channel_vault_path(channel.pk)) or {}
            val = secrets.get(key)
            if val:
                return val
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not read secret %r for channel %s: %s", key, channel.pk, exc)
    cfg = channel.config or {}
    return cfg.get(key) or ""


def delete_channel_secrets(channel_id) -> None:
    from apps.credentials import vault
    try:
        vault.delete_secret(channel_vault_path(channel_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not delete secrets for channel %s: %s", channel_id, exc)
