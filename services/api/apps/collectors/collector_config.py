"""Assemble the per-collector desired-state config bundle (config-DOWN).

A remote collector polls/forwards for the devices of its assigned sites (plus any
devices pinned to it directly). This builds the NON-SECRET bundle the collector
needs — device SNMP poll config (reusing the canonical builder, so only OpenBao
`cred_path` references travel, never key material), and the service checks it
should run. The bundle is written to a per-collector JetStream KV bucket
(see collector_publish) and watched by the collector-agent.
"""
from __future__ import annotations

import hashlib
import json


def _devices_for(collector):
    """Devices this collector owns — via the SINGLE authority (resolve.
    devices_for_collector, the inverse of effective_collector). Active/unreachable
    only, matching the poller. Never filter inline here: one resolver, no
    parallel path, so a device can't be double-claimed."""
    from apps.devices.models import Device
    from .resolve import devices_for_collector

    return devices_for_collector(collector).filter(
        status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE]
    )


def _check_payload(check) -> dict:
    return {
        "id": check.id,
        "name": check.name,
        "check_type": check.check_type,
        "host": check.host,
        "port": check.effective_port,
        "interval_seconds": check.interval_seconds,
        "timeout_seconds": check.timeout_seconds,
        "config": check.config or {},
        "collector_mode": check.collector_mode,
        "device_id": check.device_id,
        "site_id": check.site_id,
    }


def _checks_for(collector, devices) -> list[dict]:
    """Service checks the collector should run: those on its owned devices or the
    sites it's the default collector for, plus any explicitly assigned to it (the
    `selected` collector_mode)."""
    from django.db.models import Q

    from apps.checks.models import ServiceCheck

    device_ids = [d.id for d in devices]
    site_ids = list(collector.default_for_sites.values_list("id", flat=True))
    qs = ServiceCheck.objects.filter(
        Q(device_id__in=device_ids)
        | Q(site_id__in=site_ids)
        | Q(collector_assignments__collector=collector, collector_assignments__enabled=True),
        is_active=True,
    ).distinct()
    return [_check_payload(c) for c in qs]


def build_config(collector) -> dict:
    """The full desired-state bundle for `collector`.

    `revision` is a content hash over the device+check payloads (excludes the
    timestamp) so the agent can ignore no-op rewrites.
    """
    from django.utils import timezone

    from apps.devices.snmp_publish import build_device_payload

    devices = list(_devices_for(collector))
    device_payloads = [p for p in (build_device_payload(d) for d in devices) if p is not None]
    checks = _checks_for(collector, devices)

    content = json.dumps({"devices": device_payloads, "checks": checks},
                         sort_keys=True, separators=(",", ":")).encode()
    revision = hashlib.sha256(content).hexdigest()[:16]

    return {
        "collector_id": collector.id,
        "nats_account": collector.nats_account,
        "revision": revision,
        "generated_at": timezone.now().isoformat(),
        "devices": device_payloads,
        "checks": checks,
    }
