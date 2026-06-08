"""Keep the DiscoveredPlatformModel fleet inventory in sync with devices.

On any Device save/delete, rebuild the platform/model/version inventory (and its
cached OS-version compliance statuses). Gated by settings.OS_PLATFORM_REFRESH_ON_SAVE
(off in tests) and deferred to transaction commit so it never sees rolled-back
rows. The scheduler also refreshes every 6h as a safety net.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _refresh():
    if not getattr(settings, "OS_PLATFORM_REFRESH_ON_SAVE", True):
        return

    def _run():
        from .os_policy import refresh_discovered_platforms
        try:
            refresh_discovered_platforms()
        except Exception as exc:  # noqa: BLE001 — never break a device save on this
            logger.warning("OS policy: fleet inventory refresh failed: %s", exc)

    transaction.on_commit(_run)


def _note_new_version(instance):
    if not getattr(settings, "OS_PLATFORM_REFRESH_ON_SAVE", True):
        return

    def _run():
        from .os_policy import note_new_os_version
        try:
            note_new_os_version(instance)
        except Exception as exc:  # noqa: BLE001 — never break a device save on this
            logger.warning("OS policy: new-version detection failed: %s", exc)

    transaction.on_commit(_run)


@receiver(post_save, sender="devices.Device")
def _on_device_saved(sender, instance, **kwargs):
    # Record a placeholder + INFO alert when a never-seen OS version appears,
    # then refresh the fleet inventory.
    _note_new_version(instance)
    _refresh()


@receiver(post_delete, sender="devices.Device")
def _on_device_deleted(sender, instance, **kwargs):
    _refresh()
