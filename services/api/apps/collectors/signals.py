"""Republish a collector's config bundle when its inputs change (config-DOWN).

Ownership is single-authority (apps.collectors.resolve.effective_collector): a
device resolves to exactly one collector. When a device, its monitored
interfaces / telemetry config, a service check, or a credential profile changes,
the responsible REMOTE collector's bundle is rebuilt. On any *ownership-moving*
change — Device.collector or Site.default_collector changing — both the OLD and
NEW owners are refreshed, so the old owner drops the device and the new owner
adds it (no stale-bundle double-poll).

Best-effort, gated by settings.COLLECTOR_CONFIG_PUBLISH (off in tests), deferred
to commit so a collector never sees a rolled-back state.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(getattr(settings, "COLLECTOR_CONFIG_PUBLISH", True))


def _owner_id(device) -> int | None:
    """Id of the single REMOTE collector responsible for `device`, else None."""
    from .models import Collector
    from .resolve import effective_collector

    c = effective_collector(device)
    if c is not None and c.collector_type == Collector.CollectorType.REMOTE:
        return c.id
    return None


def _republish_ids(collector_ids) -> None:
    """Rebuild + rewrite the bundle for each given REMOTE collector (best-effort)."""
    ids = {i for i in collector_ids if i}
    if not ids or not _enabled():
        return

    def _run():
        from .collector_publish import publish_collector_config
        from .models import Collector

        for c in Collector.objects.filter(id__in=ids, collector_type=Collector.CollectorType.REMOTE):
            try:
                publish_collector_config(c)
            except Exception as exc:  # noqa: BLE001 — never break the triggering save
                logger.warning("collector config republish failed for %s: %s", c.id, exc)

    transaction.on_commit(_run)


# ── Devices: ownership-aware (old ∪ new) ─────────────────────────────────────

@receiver(pre_save, sender="devices.Device")
def _device_pre_save(sender, instance, **kwargs):
    # Stash the pre-save owner so a reassignment can refresh the OLD owner too.
    if not _enabled() or not instance.pk:
        instance._old_owner_id = None
        return
    old = (sender.objects
           .select_related("collector", "site", "site__default_collector")
           .filter(pk=instance.pk).first())
    instance._old_owner_id = _owner_id(old) if old else None


@receiver(post_save, sender="devices.Device")
def _device_post_save(sender, instance, **kwargs):
    if not _enabled():
        return
    _republish_ids({getattr(instance, "_old_owner_id", None), _owner_id(instance)})


@receiver(post_delete, sender="devices.Device")
def _device_post_delete(sender, instance, **kwargs):
    # The device is gone; tell its (former) owner to drop it.
    if not _enabled():
        return
    _republish_ids({_owner_id(instance)})


# ── Sites: default_collector move refreshes both old + new owners ────────────

@receiver(pre_save, sender="devices.Site")
def _site_pre_save(sender, instance, **kwargs):
    if not _enabled() or not instance.pk:
        instance._old_default_collector_id = None
        return
    instance._old_default_collector_id = (
        sender.objects.filter(pk=instance.pk).values_list("default_collector_id", flat=True).first()
    )


@receiver(post_save, sender="devices.Site")
def _site_post_save(sender, instance, **kwargs):
    if not _enabled():
        return
    old_id = getattr(instance, "_old_default_collector_id", None)
    new_id = instance.default_collector_id
    if old_id == new_id:
        return  # the site's default collector didn't move
    # Each rebuild reflects the moved site's devices: the old owner drops them,
    # the new owner adds them.
    _republish_ids({old_id, new_id})


# ── Config-only changes: refresh the current owner ───────────────────────────

def _republish_for_device(device):
    if device is not None:
        _republish_ids({_owner_id(device)})


@receiver(post_save, sender="telemetry.MonitoredInterface")
@receiver(post_save, sender="telemetry.TelemetryConfig")
def _on_telemetry_changed(sender, instance, **kwargs):
    if not _enabled():
        return
    _republish_for_device(getattr(instance, "device", None))


@receiver(post_save, sender="checks.ServiceCheck")
@receiver(post_delete, sender="checks.ServiceCheck")
def _on_check_changed(sender, instance, **kwargs):
    if not _enabled():
        return
    from apps.devices.models import Device

    from .models import Collector

    ids: set[int | None] = set()
    if instance.device_id:
        d = (Device.objects.select_related("collector", "site", "site__default_collector")
             .filter(pk=instance.device_id).first())
        if d:
            ids.add(_owner_id(d))
    if instance.site_id:
        site_owner = (Collector.objects
                      .filter(default_for_sites__id=instance.site_id,
                              collector_type=Collector.CollectorType.REMOTE)
                      .values_list("id", flat=True).first())
        ids.add(site_owner)
    # Checks pinned to specific collectors (the `selected` collector_mode).
    ids.update(
        Collector.objects.filter(service_checks=instance,
                                 collector_type=Collector.CollectorType.REMOTE)
        .values_list("id", flat=True)
    )
    _republish_ids(ids)


@receiver(post_save, sender="credentials.CredentialProfile")
def _on_credential_changed(sender, instance, **kwargs):
    if not _enabled():
        return
    from apps.devices.models import Device

    ids = {
        _owner_id(d)
        for d in (Device.objects
                  .select_related("collector", "site", "site__default_collector")
                  .filter(credential_profile=instance))
    }
    _republish_ids(ids)
