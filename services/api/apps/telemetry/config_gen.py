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
    "fortios": "fortinet_fortios",
}

# Some platforms share most templates with cisco_xe but need their own syslog
# syntax (NX-OS "logging server", IOS-XR "logging <ip>"/"hostnameprefix").
_SYSLOG_TEMPLATE = {
    "nxos": "cisco_nxos_syslog",
    "ios_xr": "cisco_xr_syslog",
}
# Sections available per family (i.e. which .j2 templates exist).
_FAMILY_SECTIONS = {
    "cisco_xe": ["snmp", "syslog", "gnmi", "netflow"],
    "cisco_xr": ["snmp", "syslog", "gnmi", "netflow"],  # fall back to cisco_xe templates
    "juniper_junos": ["snmp", "syslog"],
    "arista_eos": ["snmp", "syslog"],
    "fortinet_fortios": ["snmp", "syslog", "netflow"],  # no gNMI on FortiOS
}

SECTIONS = ["snmp", "syslog", "gnmi", "netflow"]


def _env():
    from jinja2 import Environment, FileSystemLoader
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False, trim_blocks=True, lstrip_blocks=True,
    )


def _context(device, cfg: TelemetryConfig) -> dict:
    from apps.collectors.resolve import effective_collector_ip

    profile = device.credential_profile
    creds = vault.read_secret(profile.vault_path) if (profile and profile.vault_path) else {}
    return {
        # Device's assigned collector → site default → global default →
        # settings.COLLECTOR_IP.
        "collector_ip": effective_collector_ip(device),
        "device_mgmt_ip": device.management_ip or device.ip_address or "",
        "management_interface": "Loopback0",
        "platform": (device.platform or "").lower(),
        "hostname": device.hostname or "",
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

    platform = (device.platform or "").lower()
    sections: dict[str, dict] = {}
    for sec in SECTIONS:
        if sec in available:
            # Platforms with bespoke syslog syntax get a dedicated template.
            if sec == "syslog" and platform in _SYSLOG_TEMPLATE:
                tmpl_name = _SYSLOG_TEMPLATE[platform]
            else:
                tmpl_name = f"{tmpl_family}_{sec}"
            try:
                config = env.get_template(f"{tmpl_name}.j2").render(**ctx).strip()
            except Exception as exc:
                logger.warning("template render failed for %s: %s", tmpl_name, exc)
                config = None
        else:
            config = None
        sections[sec] = {"enabled": bool(enabled_default.get(sec) and config), "config": config}

    # gNMI / push-telemetry: generated in Python (per-platform, targeted to the
    # device's monitored interfaces) rather than from a single Jinja template, so
    # every platform gets device-level + per-interface subscriptions - and
    # non-gNMI platforms (PAN-OS→OTLP, FortiOS→SNMP) get their native push config.
    from . import gnmi_subscriptions

    interfaces = list(device.monitored_interfaces.all().order_by("if_index", "if_name"))
    push_cfg = gnmi_subscriptions.generate_push_config(device, ctx["collector_ip"], interfaces, cfg)
    sections["gnmi"] = {"enabled": bool(enabled_default.get("gnmi")), "config": push_cfg}

    # Sanitise every section + the full config to ASCII so the Copy button
    # yields paste-safe text (non-ASCII like em dashes cause "% Invalid input"
    # on Cisco IOS/IOS-XE).
    for sec in SECTIONS:
        if sections[sec]["config"]:
            sections[sec]["config"] = sanitize_config_for_push(sections[sec]["config"])

    full = "\n!\n".join(
        f"! -- {sec.upper()} --\n{sections[sec]['config']}"
        for sec in SECTIONS if sections[sec]["config"]
    )

    return {
        "platform": device.platform or "",
        "vendor": device.vendor or "",
        "collector_ip": ctx["collector_ip"],
        "sections": sections,
        "full_config": sanitize_config_for_push(full),
    }


# Common unicode → ASCII substitutions; the final encode() catches anything else.
_UNICODE_TO_ASCII = {
    "—": "-", "–": "-",      # em / en dash
    "‘": "'", "’": "'",      # single quotes
    "“": '"', "”": '"',      # double quotes
    "…": "...",                    # ellipsis
    " ": " ",                      # non-breaking space
    "─": "-",                      # box-drawing horizontal
}


def sanitize_config_for_push(config: str) -> str:
    """
    Replace non-ASCII characters with ASCII equivalents so the config is safe to
    paste or push to a device. Any remaining non-ASCII is replaced with '?'.
    """
    if not config:
        return config
    for uni, asc in _UNICODE_TO_ASCII.items():
        config = config.replace(uni, asc)
    return config.encode("ascii", errors="replace").decode("ascii")


def section_lines(config: str) -> list[str]:
    """
    Config lines ready for Netmiko send_config_set: comment lines (starting with
    '!') are dropped — they aren't config and can trip up some platforms — and
    every line is sanitised to ASCII.
    """
    out = []
    for ln in (config or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("!"):
            continue
        out.append(sanitize_config_for_push(ln))
    return out
