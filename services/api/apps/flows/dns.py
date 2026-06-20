"""Reverse-DNS enrichment for Flow Analytics.

Resolves flow source/destination IPs to hostnames for display. Two layers:

1. **spane inventory first** — an IP that matches a Device's management_ip or
   ip_address resolves to that device's hostname immediately (no DNS needed, so
   our own switches/APs always show their curated name).
2. **Reverse DNS** for the rest, run in parallel and cached in the Django cache
   (5-min TTL) so repeated table renders don't re-hammer the resolver.

Unresolved IPs map back to themselves so the caller can always render something.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket

from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "dns_resolve_"
_CACHE_TTL = 300            # 5 minutes
_MAX_IPS = 100             # per request
_MAX_WORKERS = 20
_RESOLVE_TIMEOUT = 3.0     # seconds for the whole parallel batch


def _cache_key(ip: str) -> str:
    return f"{_CACHE_PREFIX}{ip}"


def _valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def _inventory_map(ips: list[str]) -> dict[str, str]:
    """IPs that belong to a known Device → that device's hostname."""
    from django.db.models import Q

    from apps.devices.models import Device
    if not ips:
        return {}
    rows = (Device.objects
            .filter(Q(management_ip__in=ips) | Q(ip_address__in=ips))
            .values("hostname", "management_ip", "ip_address"))
    out: dict[str, str] = {}
    for r in rows:
        for field in ("management_ip", "ip_address"):
            val = r.get(field)
            if val in ips and r["hostname"] and val not in out:
                out[val] = r["hostname"]
    return out


def _reverse_one(ip: str) -> tuple[str, str]:
    try:
        return ip, socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ip, ip   # fall back to the IP itself


def resolve_ips(ips: list[str]) -> dict:
    """Resolve a list of IPs to hostnames (inventory-first, then cached rDNS).

    Returns ``{"resolved": {ip: hostname}, "total", "cached", "resolved_now",
    "from_inventory", "failed"}``. Invalid/extra IPs are dropped (capped at 100).
    """
    # De-dupe, validate, cap.
    unique = [ip for ip in dict.fromkeys(ips) if _valid_ip(ip)][:_MAX_IPS]
    resolved: dict[str, str] = {}

    # 1) Inventory wins — and warms the cache so other views agree.
    inv = _inventory_map(unique)
    for ip, hostname in inv.items():
        resolved[ip] = hostname

    # 2) Cache for the remainder.
    pending = [ip for ip in unique if ip not in resolved]
    cached_count = 0
    to_resolve = []
    for ip in pending:
        hit = cache.get(_cache_key(ip))
        if hit is not None:
            resolved[ip] = hit
            cached_count += 1
        else:
            to_resolve.append(ip)

    # 3) Reverse DNS in parallel for the rest.
    resolved_now = 0
    failed = 0
    if to_resolve:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
                futures = {ex.submit(_reverse_one, ip): ip for ip in to_resolve}
                try:
                    for fut in concurrent.futures.as_completed(futures, timeout=_RESOLVE_TIMEOUT):
                        ip, hostname = fut.result()
                        resolved[ip] = hostname
                        cache.set(_cache_key(ip), hostname, timeout=_CACHE_TTL)
                        if hostname == ip:
                            failed += 1
                        else:
                            resolved_now += 1
                except concurrent.futures.TimeoutError:
                    logger.debug("dns resolve timed out with %d pending", len(to_resolve))
        except Exception as exc:  # noqa: BLE001 — resolution is best-effort
            logger.warning("dns resolve batch failed: %s", exc)

    # Any IP we never got to (timeout) maps to itself.
    for ip in unique:
        resolved.setdefault(ip, ip)
        if resolved[ip] == ip and ip not in inv:
            pass

    return {
        "resolved": resolved,
        "total": len(unique),
        "cached": cached_count,
        "resolved_now": resolved_now,
        "from_inventory": len(inv),
        "failed": failed,
    }


def clear_cache() -> int:
    """Drop all dns_resolve_* cache entries. Returns best-effort count cleared.

    LocMemCache (the default) has no key iteration, so we clear the whole cache
    as a fallback; Redis/Memcached back-ends prune only the prefixed keys.
    """
    backend = cache
    # Redis (django-redis) exposes delete_pattern.
    delete_pattern = getattr(backend, "delete_pattern", None)
    if callable(delete_pattern):
        try:
            return int(delete_pattern(f"{_CACHE_PREFIX}*") or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dns cache delete_pattern failed: %s", exc)
    # Fallback: clear the entire cache (DNS entries are cheap to re-resolve).
    try:
        backend.clear()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dns cache clear failed: %s", exc)
    return -1
