"""Auto-register (and heartbeat) this server as the local collector.

Idempotent: every NetPulse server is itself a collector — it receives all
telemetry locally (SNMP/SSH/syslog/NetFlow/gRPC) and, in a single-server
deployment, is the only collector. Registering it gives multi-collector
features (device→collector assignment, per-site polling, health) a concrete
anchor and a sensible global default.

Run on startup (entrypoint + run_scheduler) and on every scheduler tick to
refresh the heartbeat (last_seen_at). See apps.collectors.models.Collector.
"""
from __future__ import annotations

import logging
import socket

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.collectors.models import LOCAL_API_KEY_SENTINEL, Collector

logger = logging.getLogger(__name__)

LOCAL_CAPABILITIES = {
    "snmp": True,
    "ssh": True,
    "syslog": True,
    "netflow": True,
    "grpc": True,
}


def _server_ip() -> str | None:
    """The host's LAN IP (NETPULSE_HOST_IP / COLLECTOR_IP / allowed-hosts / route).

    Devices must reach this IP, so it has to be the HOST IP — not the container
    IP that bare socket detection returns inside Docker. See host_ip.get_host_ip.
    """
    from apps.collectors.host_ip import get_host_ip
    return get_host_ip()


def register_local_collector() -> Collector:
    """Create/update the single LOCAL collector row and stamp its heartbeat."""
    from apps.core.views import _netpulse_version

    defaults = {
        "name": "NetPulse Local",
        "hostname": socket.getfqdn(),
        "collector_ip": _server_ip(),
        "version": _netpulse_version(),
        "status": Collector.Status.ACTIVE,
        "capabilities": LOCAL_CAPABILITIES,
        "last_seen_at": timezone.now(),
        "api_key_hash": LOCAL_API_KEY_SENTINEL,
    }
    collector, _created = Collector.objects.update_or_create(
        collector_type=Collector.CollectorType.LOCAL, defaults=defaults,
    )
    # Self-heal a stored Docker-bridge IP (172.16.0.0/12) left by an older build
    # that detected the container IP — replace it with the real host IP if we can
    # resolve one that isn't itself a container address.
    from apps.collectors.host_ip import is_docker_ip
    if is_docker_ip(collector.collector_ip):
        real = _server_ip()
        if real and not is_docker_ip(real):
            logger.info("Corrected collector IP %s → %s (was a Docker bridge address)",
                        collector.collector_ip, real)
            collector.collector_ip = real
            collector.save(update_fields=["collector_ip"])
    # Make the local server the global default when no other default exists, so
    # devices with no explicit collector resolve to it (see resolve.py).
    if not Collector.objects.filter(is_default=True).exclude(pk=collector.pk).exists():
        if not collector.is_default:
            collector.is_default = True
            collector.save(update_fields=["is_default"])
    return collector


class Command(BaseCommand):
    help = "Register/refresh this server as the local NetPulse collector."

    def handle(self, *args, **options):
        c = register_local_collector()
        self.stdout.write(self.style.SUCCESS(
            f"Local collector registered: {c.name} ({c.hostname or 'no-hostname'}, "
            f"ip={c.collector_ip or 'unset'}, default={c.is_default})"
        ))
