"""
Netmiko-based platform auto-detection.

Runs SSHDetect to guess a device's Netmiko ``device_type``, maps it to NetPulse
vendor/platform, then connects with the detected type to pull OS version, model
and serial from ``show version``. Network-touching steps (``_ssh_detect`` and
``_collect_version``) are the seams tests monkeypatch; netmiko is imported
lazily so this module loads without it.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

NETMIKO_TO_NETPULSE = {
    "cisco_ios": {"vendor": "cisco", "platform": "ios"},
    "cisco_xe": {"vendor": "cisco", "platform": "ios_xe"},
    "cisco_xr": {"vendor": "cisco", "platform": "ios_xr"},
    "cisco_nxos": {"vendor": "cisco", "platform": "nxos"},
    "cisco_asa": {"vendor": "cisco", "platform": "asa"},
    "juniper_junos": {"vendor": "juniper", "platform": "junos"},
    "arista_eos": {"vendor": "arista", "platform": "eos"},
    "fortinet": {"vendor": "fortinet", "platform": "fortios"},
    "paloalto_panos": {"vendor": "paloalto", "platform": "panos"},
    "linux": {"vendor": "linux", "platform": "linux"},
    "vyos": {"vendor": "vyos", "platform": "vyos"},
}

# Targeted version commands; falls back to plain "show version".
VERSION_COMMANDS = {
    "cisco_ios": "show version",
    "cisco_xe": "show version",
    "cisco_xr": "show version brief",
    "cisco_nxos": "show version",
    "juniper_junos": "show version",
    "arista_eos": "show version",
}


def _ssh_detect(ip: str, username: str, password: str, port: int):
    """Return (best_match, potential_matches dict). Network seam."""
    from netmiko import SSHDetect
    guesser = SSHDetect(
        device_type="autodetect", host=ip, username=username,
        password=password, port=port,
    )
    best = guesser.autodetect()
    return best, dict(getattr(guesser, "potential_matches", {}) or {})


def _collect_version(device_type: str, ip: str, username: str, password: str, port: int) -> dict:
    """Connect with the detected type and parse show-version. Network seam."""
    from netmiko import ConnectHandler
    conn = ConnectHandler(
        device_type=device_type, host=ip, username=username,
        password=password, port=port, fast_cli=False,
    )
    try:
        prompt = conn.find_prompt()
        output = conn.send_command(VERSION_COMMANDS.get(device_type, "show version"), read_timeout=60)
    finally:
        conn.disconnect()
    return parse_version(output, prompt)


def parse_version(output: str, prompt: str = "") -> dict:
    """Best-effort extraction of os_version / model / serial / hostname."""
    def first(*patterns):
        for pat in patterns:
            m = re.search(pat, output, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    hostname = prompt.rstrip("#>$ ").strip() or None
    return {
        "os_version": first(r"Version\s+([\w.()\-]+)", r"JUNOS\s+([\w.\-]+)", r"\bEOS\b.*?([\d][\w.\-]+)"),
        "model": first(r"Model number\s*:\s*(\S+)", r"cisco\s+(\S+)\s+.*processor",
                       r"Hardware:\s*(\S+)", r"Model:\s*(\S+)", r"^cisco\s+(\S+)"),
        "serial": first(r"System serial number\s*:\s*(\S+)", r"Processor board ID\s+(\S+)",
                        r"Serial number:\s*(\S+)", r"Serial Number\s*:\s*(\S+)"),
        "hostname": hostname,
    }


def _confidence(matches: dict, best: str) -> str:
    scores = sorted((matches or {}).values(), reverse=True)
    top = scores[0] if scores else 0
    second = scores[1] if len(scores) > 1 else 0
    if top >= 99 or (top - second) >= 50:
        return "high"
    if top >= 7:
        return "medium"
    return "low"


def _classify_error(exc: Exception, best_guess=None) -> dict:
    name = type(exc).__name__
    if "Auth" in name:
        code = "auth_failed"
    elif "Timeout" in name or "timeout" in str(exc).lower():
        code = "timeout"
    else:
        code = "unknown"
    logger.warning("platform detection failed (%s): %s", code, exc)
    return {"detected": False, "error": code, "best_guess": best_guess}


# SSH-banner substrings → (netmiko device_type, vendor, platform) for vendors
# Netmiko SSHDetect doesn't reliably fingerprint. Checked case-insensitively.
_BANNER_PLATFORM_HINTS = [
    ("fortios", "fortinet", "fortios"),
    ("fortigate", "fortinet", "fortios"),
    ("fortinet", "fortinet", "fortios"),
    ("palo alto", "paloalto", "panos"),
    ("pan-os", "paloalto", "panos"),
]


def _banner_platform(ip: str, port: int) -> dict | None:
    """Infer platform from the SSH banner when SSHDetect can't (FortiOS/PAN-OS)."""
    from . import fingerprint
    banner = fingerprint._ssh_banner(ip, 3.0).lower() if port == 22 else ""
    if not banner:
        return None
    for needle, vendor, platform in _BANNER_PLATFORM_HINTS:
        if needle in banner:
            return {"device_type": vendor, "vendor": vendor, "platform": platform}
    return None


def detect_platform(ip: str, ssh_username: str, ssh_password: str, ssh_port: int = 22) -> dict:
    """
    Auto-detect a device's platform. Returns a result dict — never raises.

    Success: {detected, device_type, vendor, platform, os_version, hostname,
              model, serial, confidence, all_matches}
    Failure: {detected: false, error: timeout|auth_failed|unknown, best_guess?}
    """
    try:
        best, matches = _ssh_detect(ip, ssh_username, ssh_password, ssh_port or 22)
    except Exception as exc:
        return _classify_error(exc)

    if not best:
        # SSHDetect frequently can't fingerprint FortiOS / PAN-OS. Fall back to
        # the SSH banner, which carries vendor strings ("FortiGate", "Palo Alto").
        guess = _banner_platform(ip, ssh_port or 22)
        if guess:
            return {
                "detected": True, "device_type": guess["device_type"],
                "vendor": guess["vendor"], "platform": guess["platform"],
                "os_version": None, "hostname": None, "model": None, "serial": None,
                "confidence": "low", "all_matches": list((matches or {}).keys()),
            }
        return {"detected": False, "error": "unknown", "best_guess": None,
                "all_matches": list((matches or {}).keys())}

    mapping = NETMIKO_TO_NETPULSE.get(best, {"vendor": "", "platform": "other"})

    info: dict = {}
    try:
        info = _collect_version(best, ip, ssh_username, ssh_password, ssh_port or 22) or {}
    except Exception as exc:
        # Detection still succeeded; version details are best-effort.
        logger.info("version collection failed for %s (%s): %s", ip, best, exc)

    return {
        "detected": True,
        "device_type": best,
        "vendor": mapping["vendor"],
        "platform": mapping["platform"],
        "os_version": info.get("os_version"),
        "hostname": info.get("hostname"),
        "model": info.get("model"),
        "serial": info.get("serial"),
        "confidence": _confidence(matches, best),
        "all_matches": list((matches or {}).keys()),
    }
