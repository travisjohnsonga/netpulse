"""
Telemetry config generation.

Renders platform-appropriate SNMP/Syslog/gNMI/NetFlow snippets from a device's
TelemetryConfig + credential profile + the configured collector IP, using Jinja2
templates under templates/telemetry/. Config is generated for every section a
platform supports so each can be previewed/pushed; the ``enabled`` flag reflects
the recommended default from the device's collection method.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from apps.credentials import vault
from .models import TelemetryConfig

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates" / "telemetry"

# device.platform → template family. Cisco variants share the cisco_xe templates.
_PLATFORM_FAMILY = {
    "ios": "cisco_xe", "ios_xe": "cisco_xe", "ios_xr": "cisco_xr",
    "nxos": "cisco_xe", "asa": "cisco_xe",
    "junos": "juniper_junos", "eos": "arista_eos",
}
# Sections available per family (i.e. which .j2 templates exist).
_FAMILY_SECTIONS = {
    "cisco_xe": ["snmp", "syslog", "gnmi", "netflow"],
    "cisco_xr": ["snmp", "syslog", "gnmi", "netflow"],  # fall back to cisco_xe templates
    "juniper_junos": ["snmp", "syslog"],
    "arista_eos": ["snmp", "syslog"],
}

SECTIONS = ["snmp", "syslog", "gnmi", "netflow"]


def _env():
    from jinja2 import Environment, FileSystemLoader
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False, trim_blocks=True, lstrip_blocks=True,
    )


def _context(device, cfg: TelemetryConfig) -> dict:
    profile = device.credential_profile
    creds = vault.read_secret(profile.vault_path) if (profile and profile.vault_path) else {}
    return {
        "collector_ip": getattr(settings, "COLLECTOR_IP", "") or "",
        "management_interface": "Loopback0",
        "snmpv3": bool(profile and profile.snmpv3_enabled),
        "group_name": "V3GROUP",
        "username": (profile.snmpv3_username if profile else "") or "netpulse",
        "community": creds.get("snmpv2c_community") or "netpulse",
        "auth_protocol": (profile.snmpv3_auth_protocol if profile else "") or "sha",
        "priv_protocol": (profile.snmpv3_priv_protocol if profile else "") or "aes 128",
        "auth_key": creds.get("snmpv3_auth_key") or "<AUTH_KEY>",
        "priv_key": creds.get("snmpv3_priv_key") or "<PRIV_KEY>",
        # cisco_xe gNMI uses centiseconds (periodic 3000 == 30s)
        "gnmi_interval_centisecs": (cfg.gnmi_interval or 30) * 100,
    }


def generate(device) -> dict:
    """Build the full generated-config structure for a device."""
    cfg, _ = TelemetryConfig.objects.get_or_create(device=device)
    family = _PLATFORM_FAMILY.get((device.platform or "").lower())
    ctx = _context(device, cfg)
    env = _env()

    # cisco_xr reuses cisco_xe template files.
    tmpl_family = "cisco_xe" if family == "cisco_xr" else family
    available = set(_FAMILY_SECTIONS.get(family, [])) if family else set()

    enabled_default = {
        "snmp": cfg.primary_method in (TelemetryConfig.Method.SNMP, TelemetryConfig.Method.BOTH),
        "gnmi": cfg.primary_method in (TelemetryConfig.Method.GNMI, TelemetryConfig.Method.BOTH),
        "syslog": True,
        "netflow": False,
    }

    sections: dict[str, dict] = {}
    for sec in SECTIONS:
        if sec in available:
            try:
                config = env.get_template(f"{tmpl_family}_{sec}.j2").render(**ctx).strip()
            except Exception as exc:
                logger.warning("template render failed for %s_%s: %s", tmpl_family, sec, exc)
                config = None
        else:
            config = None
        sections[sec] = {"enabled": bool(enabled_default.get(sec) and config), "config": config}

    full = "\n!\n".join(
        f"! ── {sec.upper()} ──\n{sections[sec]['config']}"
        for sec in SECTIONS if sections[sec]["config"]
    )

    return {
        "platform": device.platform or "",
        "vendor": device.vendor or "",
        "collector_ip": ctx["collector_ip"],
        "sections": sections,
        "full_config": full,
    }


def section_lines(config: str) -> list[str]:
    """Split a rendered section into non-empty config lines for send_config_set."""
    return [ln for ln in (config or "").splitlines() if ln.strip()]
