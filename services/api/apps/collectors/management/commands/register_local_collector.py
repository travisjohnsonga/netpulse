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

import socket

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.collectors.models import LOCAL_API_KEY_SENTINEL, Collector

LOCAL_CAPABILITIES = {
    "snmp": True,
    "ssh": True,
    "syslog": True,
    "netflow": True,
    "grpc": True,
}


def _server_ip() -> str | None:
    """Configured COLLECTOR_IP, else a best-effort primary IP, else None."""
    from django.conf import settings

    configured = getattr(settings, "COLLECTOR_IP", "") or ""
    if configured:
        return configured
    # Best-effort: the outbound-route source address (no traffic actually sent).
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


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
