"""
Resolve which collector (and thus which IP) a device's telemetry targets.

Precedence: the device's own collector → its site's default collector → the
global default collector (is_default=True) → settings.COLLECTOR_IP.
"""
from __future__ import annotations


def effective_collector(device):
    """Return the Collector that should monitor ``device``, or None."""
    if getattr(device, "collector_id", None):
        return device.collector
    site = getattr(device, "site", None)
    if site is not None and getattr(site, "default_collector_id", None):
        return site.default_collector
    from .models import Collector
    return Collector.objects.filter(is_default=True).first()


def devices_for_collector(collector):
    """The inverse of :func:`effective_collector`: a queryset of every device
    that resolves to ``collector``.

    This is the SINGLE authority for "which devices does this collector own" —
    build_config (and any future caller) must go through it rather than filter
    devices inline, so a device can never be claimed by two collectors. The
    precedence mirrors effective_collector exactly, expressed as one efficient
    query (no per-device Python evaluation):

      device.collector == c
      OR (device.collector is null AND site.default_collector == c)
      OR (c.is_default AND device.collector is null
          AND (site is null OR site.default_collector is null))
    """
    from django.db.models import Q

    from apps.devices.models import Device

    cond = Q(collector=collector)
    cond |= Q(collector__isnull=True, site__default_collector=collector)
    if collector.is_default:
        cond |= Q(collector__isnull=True) & (
            Q(site__isnull=True) | Q(site__default_collector__isnull=True)
        )
    return (
        Device.objects
        .select_related("collector", "site", "credential_profile", "telemetry_config")
        .prefetch_related("monitored_interfaces")
        .filter(cond)
        .distinct()
    )


def effective_collector_ip(device) -> str:
    """Resolve the IP a device should send telemetry to (never raises)."""
    collector = effective_collector(device)
    if collector and collector.collector_ip:
        return collector.collector_ip
    # Fall back to the detected HOST IP (NETPULSE_HOST_IP / COLLECTOR_IP / allowed
    # hosts) so generated configs never point devices at a container IP.
    from .host_ip import get_host_ip
    return get_host_ip() or ""
