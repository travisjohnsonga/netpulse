"""
Best-effort detection of the HOST's IP — correct from inside a container.

Socket/route-based detection run *inside* a Docker container returns the
CONTAINER IP (e.g. 172.18.x.x) or the bridge gateway, not the host's LAN IP that
devices must send telemetry to. So we prefer values captured on the host by
setup.sh (NETPULSE_HOST_IP / COLLECTOR_IP) or the configured allowed-hosts list,
and only fall back to source-route detection (warning that it may be wrong).
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket

logger = logging.getLogger(__name__)

# Docker's default bridge address pools live in 172.16.0.0/12 — an IP here is
# almost certainly a container/bridge address, not the host's LAN IP.
_DOCKER_RANGE = ipaddress.ip_network("172.16.0.0/12")


def _valid_ip(value: str) -> str:
    """Return the stripped value if it's a valid IP address, else ''."""
    value = (value or "").strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return ""


def is_docker_ip(value) -> bool:
    """True when ``value`` is a Docker bridge address (172.16.0.0/12)."""
    ip = _valid_ip(str(value))
    return bool(ip) and ipaddress.ip_address(ip) in _DOCKER_RANGE


def get_host_ip() -> str | None:
    """
    Resolve the host's LAN IP using, in order:
      1. NETPULSE_HOST_IP   — explicit override set by setup.sh on the host.
      2. COLLECTOR_IP       — also host-detected by setup.sh.
      3. first non-loopback IP in DJANGO_ALLOWED_HOSTS (setup.sh adds the host IP).
      4. source-route IP    — unreliable inside a container; logged as a warning.
    Returns None only when nothing usable is found.
    """
    from django.conf import settings

    ip = _valid_ip(os.environ.get("NETPULSE_HOST_IP", ""))
    if ip:
        return ip

    ip = _valid_ip(getattr(settings, "COLLECTOR_IP", "") or "")
    if ip and not ip.startswith("127."):
        return ip

    for host in (getattr(settings, "ALLOWED_HOSTS", []) or []):
        ip = _valid_ip(host)
        if ip and not ip.startswith("127."):
            return ip

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        logger.warning("Falling back to source-route IP %s for the host IP — inside a "
                       "container this may be the container IP. Set NETPULSE_HOST_IP in .env.", ip)
        return ip
    except OSError:
        logger.warning("Could not determine the host IP. Set NETPULSE_HOST_IP in .env.")
        return None
