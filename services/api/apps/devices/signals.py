"""
Keep the ingest-snmp poller in sync with the device inventory.

On any change that affects SNMP polling — a Device, its TelemetryConfig, its
MonitoredInterfaces, or its CredentialProfile — republish the device's config
to NATS. On device delete, publish a removal. All publishing is best-effort and
gated by settings.SNMP_DEVICE_PUBLISH (off in tests).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _publish_device(device):
    from . import snmp_publish
    # Publish after the surrounding transaction commits so the poller never
    # sees a row the DB later rolls back.
    transaction.on_commit(lambda: snmp_publish.publish_device_upsert(device))


def republish_device(device_id) -> None:
    """Republish a device by id once the transaction commits — for writes that
    don't fire post_save (bulk_create / queryset.update / delete). Re-reads the
    device at commit time and skips it if it no longer exists, so a cascade
    delete can't re-add a device the poller was just told to remove."""
    # One publish per device per transaction: replacing N interfaces fires N
    # post_delete signals, but only the committed end state matters. Dedupe
    # against Django's own pending on-commit queue rather than a side set:
    # Django drops queued callbacks on rollback, so a rolled-back transaction
    # can't leave the device marked "pending" and suppress later publishes.
    conn = transaction.get_connection()
    if any(getattr(func, "_snmp_republish_id", None) == device_id
           for _sids, func, *_ in conn.run_on_commit):
        return

    def _go():
        from . import snmp_publish
        from .models import Device
        device = Device.objects.filter(pk=device_id).first()
        if device is not None:
            snmp_publish.publish_device_upsert(device)
    _go._snmp_republish_id = device_id
    transaction.on_commit(_go)


@receiver(post_save, sender="devices.Device")
def _on_device_saved(sender, instance, **kwargs):
    _publish_device(instance)


@receiver(post_delete, sender="devices.Device")
def _on_device_deleted(sender, instance, **kwargs):
    from . import snmp_publish
    dev_id = instance.id
    transaction.on_commit(lambda: snmp_publish.publish_device_remove(dev_id))


@receiver(post_save, sender="telemetry.TelemetryConfig")
def _on_telemetry_config_saved(sender, instance, **kwargs):
    if instance.device_id:
        _publish_device(instance.device)


@receiver(post_save, sender="telemetry.MonitoredInterface")
def _on_monitored_interface_saved(sender, instance, **kwargs):
    if instance.device_id:
        _publish_device(instance.device)


@receiver(post_delete, sender="telemetry.MonitoredInterface")
def _on_monitored_interface_deleted(sender, instance, **kwargs):
    if instance.device_id:
        republish_device(instance.device_id)


@receiver(post_save, sender="credentials.CredentialProfile")
def _on_credential_profile_saved(sender, instance, **kwargs):
    # Republish every device using this profile (protocol/username may have
    # changed; the poller re-fetches key material from OpenBao itself).
    for device in instance.devices.all():
        _publish_device(device)
