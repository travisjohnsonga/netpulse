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
# NX-OS SNMPv3 user/group syntax differs from IOS-XE (no view/group RW lines,
# "aes-128" not "aes 128"), so it gets a dedicated SNMP template.
_SNMP_TEMPLATE = {
    "nxos": "cisco_nxos_snmp",
}
# Override the protocol-token family for platforms whose SNMP syntax differs
# from their general template family (NX-OS shares cisco_xe but spells priv
# protocols "aes-128").
_SNMP_TOKEN_FAMILY = {
    "nxos": "cisco_nxos",
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


# ── SNMPv3 protocol token mapping (per template family) ───────────────────────
# Network OSes spell the same crypto algorithm differently on the CLI. Normalise
# the credential profile's free-text protocol to a canonical key, then render the
# family-specific token. authPriv (auth + priv) is the default security level.
def _canon_auth(p: str) -> str:
    p = (p or "").lower().replace("-", "").replace(" ", "")
    for k in ("sha512", "sha384", "sha256", "sha224"):
        if k in p:
            return k
    if "sha" in p:
        return "sha"
    if "md5" in p:
        return "md5"
    return "sha"  # default


def _canon_priv(p: str) -> str:
    p = (p or "").lower().replace("-", "").replace(" ", "")
    if "aes256" in p:
        return "aes256"
    if "aes192" in p:
        return "aes192"
    if "aes128" in p or p == "aes":
        return "aes128"
    if "3des" in p:
        return "3des"
    if "des" in p:
        return "des"
    return "aes128"  # default


_AUTH_CLI = {
    "cisco_xe":      {"md5": "md5", "sha": "sha", "sha256": "sha", "sha384": "sha", "sha512": "sha", "sha224": "sha"},
    "cisco_nxos":    {"md5": "md5", "sha": "sha", "sha256": "sha", "sha384": "sha", "sha512": "sha", "sha224": "sha"},
    "juniper_junos": {"md5": "authentication-md5", "sha": "authentication-sha1", "sha224": "authentication-sha224",
                      "sha256": "authentication-sha256", "sha384": "authentication-sha256", "sha512": "authentication-sha256"},
    "arista_eos":    {"md5": "md5", "sha": "sha", "sha256": "sha", "sha384": "sha", "sha512": "sha", "sha224": "sha"},
    "fortinet_fortios": {"md5": "md5", "sha": "sha1", "sha224": "sha224", "sha256": "sha256", "sha384": "sha384", "sha512": "sha512"},
}
_PRIV_CLI = {
    "cisco_xe":      {"des": "des", "3des": "3des", "aes128": "aes 128", "aes192": "aes 192", "aes256": "aes 256"},
    "cisco_nxos":    {"des": "des", "3des": "des", "aes128": "aes-128", "aes192": "aes-128", "aes256": "aes-128"},
    "juniper_junos": {"des": "privacy-des", "3des": "privacy-3des", "aes128": "privacy-aes128",
                      "aes192": "privacy-aes128", "aes256": "privacy-aes128"},
    "arista_eos":    {"des": "des", "3des": "des", "aes128": "aes", "aes192": "aes192", "aes256": "aes256"},
    "fortinet_fortios": {"des": "des", "3des": "des", "aes128": "aes", "aes192": "aes", "aes256": "aes256"},
}


def _snmpv3_tokens(family: str, profile) -> tuple[str, str]:
    """(auth_cli, priv_cli) tokens for the given template family + profile."""
    fam = family or "cisco_xe"
    auth = _canon_auth(profile.snmpv3_auth_protocol if profile else "")
    priv = _canon_priv(profile.snmpv3_priv_protocol if profile else "")
    auth_cli = _AUTH_CLI.get(fam, _AUTH_CLI["cisco_xe"]).get(auth, auth)
    priv_cli = _PRIV_CLI.get(fam, _PRIV_CLI["cisco_xe"]).get(priv, priv)
    return auth_cli, priv_cli


def _context(device, cfg: TelemetryConfig, family: str | None = None) -> dict:
    from apps.collectors.resolve import effective_collector_ip

    profile = device.credential_profile
    creds = vault.read_secret(profile.vault_path) if (profile and profile.vault_path) else {}
    auth_cli, priv_cli = _snmpv3_tokens(family, profile)
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
        "auth_protocol": auth_cli,
        "priv_protocol": priv_cli,
        # Keys are write-only: the real values live in OpenBao and are only
        # injected when actually pushing. Generated/previewed config shows a
        # placeholder the engineer fills in (or the push path substitutes).
        "auth_key": creds.get("snmpv3_auth_key") or "YOUR-AUTH-KEY-HERE",
        "priv_key": creds.get("snmpv3_priv_key") or "YOUR-PRIV-KEY-HERE",
        # cisco_xe gNMI uses centiseconds (periodic 3000 == 30s)
        "gnmi_interval_centisecs": (cfg.gnmi_interval or 30) * 100,
    }


def generate(device) -> dict:
    """Build the full generated-config structure for a device."""
    cfg, _ = TelemetryConfig.objects.get_or_create(device=device)
    platform = (device.platform or "").lower()
    family = _PLATFORM_FAMILY.get(platform)
    token_family = _SNMP_TOKEN_FAMILY.get(platform, family)
    ctx = _context(device, cfg, token_family)
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
            # Platforms with bespoke syslog/snmp syntax get a dedicated template.
            if sec == "syslog" and platform in _SYSLOG_TEMPLATE:
                tmpl_name = _SYSLOG_TEMPLATE[platform]
            elif sec == "snmp" and platform in _SNMP_TEMPLATE:
                tmpl_name = _SNMP_TEMPLATE[platform]
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

    # Security warning: SNMP enabled but the credential is not SNMPv3. v2c
    # community strings cross the wire in plaintext — flag it in the UI.
    snmp_warning = ""
    if sections["snmp"]["config"] and not ctx["snmpv3"]:
        snmp_warning = (
            "This device uses an SNMPv2c credential. v2c community strings are "
            "sent in plaintext and offer no authentication or encryption. Use an "
            "SNMPv3 (authPriv) credential profile for production devices."
        )

    return {
        "platform": device.platform or "",
        "vendor": device.vendor or "",
        "collector_ip": ctx["collector_ip"],
        "snmpv3": ctx["snmpv3"],
        "snmp_warning": snmp_warning,
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
