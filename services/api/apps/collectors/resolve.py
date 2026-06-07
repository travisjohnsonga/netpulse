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


def effective_collector_ip(device) -> str:
    """Resolve the IP a device should send telemetry to (never raises)."""
    collector = effective_collector(device)
    if collector and collector.collector_ip:
        return collector.collector_ip
    # Fall back to the detected HOST IP (NETPULSE_HOST_IP / COLLECTOR_IP / allowed
    # hosts) so generated configs never point devices at a container IP.
    from .host_ip import get_host_ip
    return get_host_ip() or ""
