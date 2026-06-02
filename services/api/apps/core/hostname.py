"""Hostname display helpers — strip a domain suffix from device hostnames.

DISPLAY ONLY. The stored Device.hostname is still used for SSH/SNMP/syslog;
these helpers only affect what is shown in the UI.

Effective config is read from SystemSetting (admin-configurable at runtime) and
falls back to the environment-based settings defaults when unset:
  - SystemSetting 'hostname_display_mode' = 'strip' | 'full'
  - SystemSetting 'domain_suffix'         = e.g. 'dnstest.local'
  - settings.STRIP_DOMAIN_FROM_HOSTNAMES / settings.DOMAIN_SUFFIX (fallback)
"""
from __future__ import annotations

from django.conf import settings


def hostname_display_config() -> tuple[bool, str]:
    """Return (strip_enabled, suffix).

    Resilient: any DB error (table/row missing during early migration, etc.)
    falls back to the settings-based default so serialization never breaks.
    """
    default_strip = bool(getattr(settings, "STRIP_DOMAIN_FROM_HOSTNAMES", False))
    default_suffix = getattr(settings, "DOMAIN_SUFFIX", "") or ""

    try:
        from .models import SystemSetting

        mode = SystemSetting.get("hostname_display_mode", None)
        if mode is None:
            return default_strip, default_suffix
        suffix = SystemSetting.get("domain_suffix", None)
        if suffix is None:
            suffix = default_suffix
        return mode == "strip", suffix or ""
    except Exception:  # noqa: BLE001 — never break serialization on DB issues
        return default_strip, default_suffix


def strip_domain(hostname: str) -> str:
    """Apply the display-only domain-strip logic to a hostname."""
    if not hostname:
        return hostname
    strip_enabled, suffix = hostname_display_config()
    if not strip_enabled:
        return hostname
    if suffix:
        dotted = "." + suffix
        if hostname.endswith(dotted):
            return hostname[: -len(dotted)]
        return hostname
    if "." in hostname:
        return hostname.split(".")[0]
    return hostname
