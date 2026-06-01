"""
Device configuration collector.

Connects to active devices over SSH (Netmiko), NETCONF (ncclient) or falls back
to generic SSH, pulls the running config, and stores a DeviceConfig snapshot with
change detection. Heavy protocol libraries are imported lazily so this module
(and the tests) load without them installed; the network-touching function
``_fetch_running_config`` is the single seam tests monkeypatch.

Security: credential *values* are fetched from OpenBao at collection time and
never logged.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import logging
import os
import re
import tempfile
import time

from django.conf import settings
from django.utils import timezone

from apps.configbackup.models import ConfigBackupSettings, DeviceConfig
from apps.credentials import vault

logger = logging.getLogger(__name__)

# NetPulse platform → Netmiko device_type.
_NETMIKO_TYPES = {
    "ios": "cisco_ios",
    "ios_xe": "cisco_xe",
    "ios_xr": "cisco_xr",
    "nxos": "cisco_nxos",
    "eos": "arista_eos",
    "junos": "juniper_junos",
    "sonic": "linux",
    "fortios": "fortinet",
    "other": "",
}

_VENDOR_FALLBACK = {
    "cisco": "cisco_ios",
    "arista": "arista_eos",
    "juniper": "juniper_junos",
}

# Running-config command per platform family.
_CONFIG_COMMANDS = {
    "junos": "show configuration | display set",
    # FortiOS has no "show running-config"; the complete config is collected with
    # "show full-configuration" (stable, full dump — good for diff/backup).
    "fortios": "show full-configuration",
    # everything else (cisco families, arista, generic) uses show running-config
}


def netmiko_device_type(vendor: str, platform: str) -> str:
    """
    Resolve a Netmiko device_type from vendor + platform. Falls back to
    "autodetect" for unknown platforms so Netmiko's SSHDetect can guess.
    """
    dt = _NETMIKO_TYPES.get((platform or "").lower(), "")
    if dt:
        return dt
    dt = _VENDOR_FALLBACK.get((vendor or "").lower(), "")
    return dt or "autodetect"


def config_command(platform: str) -> str:
    return _CONFIG_COMMANDS.get((platform or "").lower(), "show running-config")


def device_host(device) -> str:
    """Connection target: management IP if set, otherwise the primary IP."""
    return str(device.management_ip or device.ip_address)


# ── Config normalization ──────────────────────────────────────────────────────
# Lines that change every collection but carry no config meaning. Stripped
# before hashing so timestamp churn doesn't register as a config change.
_DYNAMIC_LINE_PATTERNS = [
    # IOS / IOS-XE / IOS-XR ("!" or "!!" prefix), NX-OS.
    re.compile(r"^\s*!{1,2}\s*Last configuration change at .*", re.IGNORECASE),
    re.compile(r"^\s*!\s*NVRAM config last updated .*", re.IGNORECASE),
    re.compile(r"^\s*!\s*Time:.*", re.IGNORECASE),                       # NX-OS "!Time: ..."
    re.compile(r"^\s*Building configuration\.\.\..*", re.IGNORECASE),
    re.compile(r"^\s*Current configuration\s*:\s*\d+ bytes.*", re.IGNORECASE),
    re.compile(r"^\s*ntp\s+clock-period\s+\d+.*", re.IGNORECASE),        # drifts every poll
    # Juniper "## Last commit: 2024-... by user" / "## Last changed: ...".
    re.compile(r"^\s*##\s*Last (commit|changed):.*", re.IGNORECASE),
    # Arista EOS / generic "! Generated on ..." / "! Saved at ...".
    re.compile(r"^\s*!\s*(Generated|Saved)\b.*", re.IGNORECASE),
    # FortiOS "show full-configuration" header metadata — these drift between
    # collections (build/version banner and the incrementing conf-file version)
    # without representing a real config change, and `#config-version=...` even
    # embeds the username of whoever ran the command. Strip so NetPulse's own
    # collection sessions don't register as a config change.
    re.compile(r"^\s*#config-version=.*", re.IGNORECASE),
    re.compile(r"^\s*#conf_file_ver=.*", re.IGNORECASE),
    re.compile(r"^\s*#buildno=.*", re.IGNORECASE),
    re.compile(r"^\s*#global_vdom=.*", re.IGNORECASE),
]
# Generic timestamp comment lines, e.g. "! 2024-06-14 12:00:00" or
# "! Mon Jun 14 12:00:00 2024".
_TS_DATE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
_TS_CLOCK = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")
_TS_DOW = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", re.IGNORECASE)


def _is_timestamp_comment(line: str) -> bool:
    # Treat IOS "!", Juniper "#"/"##", and ";"-style comment lines that contain
    # a date/clock/day-of-week as dynamic (so they don't trip change detection).
    s = line.strip()
    if not s or s[0] not in "!#;":
        return False
    return bool(_TS_CLOCK.search(s) or _TS_DATE.search(s) or _TS_DOW.search(s))


def normalize_config(content: str) -> str:
    """Strip dynamic/timestamp lines so the hash reflects substantive config only."""
    out = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if any(p.match(line) for p in _DYNAMIC_LINE_PATTERNS):
            continue
        if _is_timestamp_comment(line):
            continue
        out.append(line)
    return "\n".join(out)


def get_credentials(device) -> dict:
    """Fetch the device's secret material from OpenBao. Returns {} on any failure."""
    profile = device.credential_profile
    if not profile or not profile.vault_path:
        return {}
    try:
        return vault.read_secret(profile.vault_path)
    except Exception as exc:  # OpenBao unavailable, etc.
        logger.warning("could not fetch credentials for %s: %s", device.hostname, exc)
        return {}


def _fetch_via_ssh(device, profile, creds: dict) -> str:
    """Connect over SSH with Netmiko and return the running config."""
    from netmiko import ConnectHandler  # lazy

    host = device_host(device)
    device_type = netmiko_device_type(device.vendor, device.platform)
    username = profile.ssh_username if profile else ""
    port = (profile.ssh_port if profile else 22) or 22

    # Unknown platform → let Netmiko guess, then fall back to cisco_ios.
    if device_type == "autodetect":
        try:
            from netmiko import SSHDetect
            guesser = SSHDetect(
                device_type="autodetect", host=str(host), username=username,
                password=creds.get("ssh_password", ""), port=port,
            )
            device_type = guesser.autodetect() or "cisco_ios"
            logger.info("autodetected %s as %s", device.hostname, device_type)
        except Exception as exc:
            logger.warning("autodetect failed for %s (%s) — using cisco_ios", device.hostname, exc)
            device_type = "cisco_ios"

    params: dict = {
        "device_type": device_type,
        "host": str(host),
        "username": username,
        "port": port,
        "fast_cli": False,
    }

    key_file = None
    if creds.get("ssh_private_key"):
        fd, key_file = tempfile.mkstemp(prefix="netpulse-key-")
        with os.fdopen(fd, "w") as fh:
            fh.write(creds["ssh_private_key"])
        os.chmod(key_file, 0o600)
        params["use_keys"] = True
        params["key_file"] = key_file
        if creds.get("ssh_passphrase"):
            params["passphrase"] = creds["ssh_passphrase"]
    else:
        params["password"] = creds.get("ssh_password", "")

    try:
        conn = ConnectHandler(**params)
        try:
            return conn.send_command(config_command(device.platform), read_timeout=60)
        finally:
            conn.disconnect()
    finally:
        if key_file:
            try:
                os.remove(key_file)
            except OSError:
                pass


def _fetch_via_netconf(device, profile, creds: dict) -> str:
    """Connect over NETCONF with ncclient and return the configuration XML."""
    from ncclient import manager  # lazy

    host = device_host(device)
    with manager.connect(
        host=str(host),
        port=(profile.netconf_port if profile else 830) or 830,
        username=(profile.netconf_username or profile.ssh_username) if profile else "",
        password=creds.get("ssh_password", ""),
        hostkey_verify=False,
        timeout=60,
    ) as m:
        return str(m.get_config(source="running"))


def _fetch_running_config(device, creds: dict) -> str:
    """
    Dispatch to the right protocol and return the running config text.

    SSH is primary; NETCONF is used when it's the only enabled protocol. This is
    the single network seam — tests monkeypatch it.
    """
    profile = device.credential_profile
    if profile and profile.netconf_enabled and not profile.ssh_enabled:
        return _fetch_via_netconf(device, profile, creds)
    return _fetch_via_ssh(device, profile, creds)


def _mark_credential_failed(device, message: str) -> None:
    profile = device.credential_profile
    if not profile:
        return
    from apps.credentials.models import CredentialProfile
    profile.last_test_result = CredentialProfile.TestResult.FAILURE
    profile.last_test_message = message
    profile.save(update_fields=["last_test_result", "last_test_message"])


def store_config(device, content: str, collected_by: str) -> DeviceConfig | None:
    """
    Persist a running-config snapshot — but only when the *normalized* config
    differs from the last stored one. ``content_hash`` is the hash of the
    normalized text (used for change detection); ``content`` keeps the ORIGINAL
    for display. Returns the new DeviceConfig, or None when unchanged.
    """
    normalized = normalize_config(content)
    norm_hash = hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()
    prev = (
        DeviceConfig.objects
        .filter(device=device, config_type=DeviceConfig.ConfigType.RUNNING)
        .order_by("-collected_at")
        .first()
    )
    if prev is not None and prev.content_hash == norm_hash:
        return None  # unchanged — nothing to store

    changed = prev is not None
    diff_summary = None
    if changed:
        diff = difflib.unified_diff(
            normalize_config(prev.content).splitlines(), normalized.splitlines(),
            fromfile="previous", tofile="current", lineterm="", n=2,
        )
        diff_summary = "\n".join(list(diff)[:500])

    cb = ConfigBackupSettings.load()
    local_path = ""
    if cb.local_enabled:
        local_path = f"{cb.local_path.rstrip('/')}/{device.hostname}-running.cfg"

    return DeviceConfig.objects.create(
        device=device,
        config_type=DeviceConfig.ConfigType.RUNNING,
        collected_at=timezone.now(),
        collected_by=collected_by,
        content=content,            # ORIGINAL content for display
        content_hash=norm_hash,     # NORMALIZED hash for change detection
        changed_from_previous=changed,
        diff_summary=diff_summary,
        local_path=local_path,
    )


def publish_collected(device_id) -> None:
    """Best-effort NATS publish to netpulse.config.{device_id}.collected."""
    try:
        asyncio.run(_publish(device_id))
    except Exception as exc:
        logger.warning("NATS publish failed for device %s: %s", device_id, exc)


async def _publish(device_id) -> None:
    import nats  # lazy
    nc = await nats.connect(
            os.environ.get("NATS_URL", "nats://nats:4222"),
            user=os.environ.get("NATS_USER") or None,
            password=os.environ.get("NATS_PASSWORD") or None,
        )
    try:
        await nc.publish(f"netpulse.config.{device_id}.collected", b"{}")
    finally:
        await nc.drain()


def collect_one(device, collected_by: str = "scheduled") -> dict:
    """
    Collect one device's running config. Returns a result dict:
      {"ok": True, "stored": bool, "changed": bool, "config": DeviceConfig|None}
      {"ok": False, "error": "auth_failed"|"timeout"|"empty"|"error"}

    Updates ``device.last_seen`` whenever the device is reached, regardless of
    whether the config changed. Never raises — a single device must not stop the
    loop.
    """
    start = time.monotonic()
    creds = get_credentials(device)
    try:
        content = _fetch_running_config(device, creds)
    except Exception as exc:
        name = type(exc).__name__
        if "Auth" in name:
            _mark_credential_failed(device, f"{name}: authentication failed")
            logger.error("auth failure collecting %s (%s)", device.hostname, name)
            return {"ok": False, "error": "auth_failed"}
        if "Timeout" in name or "timeout" in str(exc).lower():
            logger.warning("timeout collecting %s: %s", device.hostname, exc)
            return {"ok": False, "error": "timeout"}
        logger.error("error collecting %s: %s", device.hostname, exc)
        return {"ok": False, "error": "error"}

    if not content or not content.strip():
        logger.warning("empty config returned for %s — skipping", device.hostname)
        return {"ok": False, "error": "empty"}

    cfg = store_config(device, content, collected_by)

    # Reachability bookkeeping happens regardless of whether the config changed.
    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])
    publish_collected(device.id)

    # Opportunistic LLDP topology discovery when an SNMP credential is configured.
    profile = device.credential_profile
    if profile and (profile.snmpv2c_enabled or profile.snmpv3_enabled):
        try:
            from apps.devices.topology import discover_links
            discover_links(device)
        except Exception as exc:
            logger.warning("topology discovery skipped for %s: %s", device.hostname, exc)

    elapsed = time.monotonic() - start
    if cfg is None:
        logger.info("config unchanged for %s (%s) — %.2fs", device.hostname, device.platform or "?", elapsed)
        return {"ok": True, "stored": False, "changed": False, "config": None}

    if cfg.changed_from_previous:
        logger.info("config changed, stored for %s (%s) — %d bytes, %.2fs",
                    device.hostname, device.platform or "?", len(content), elapsed)
    else:
        logger.info("config stored (initial baseline) for %s (%s) — %d bytes, %.2fs",
                    device.hostname, device.platform or "?", len(content), elapsed)
    return {"ok": True, "stored": True, "changed": cfg.changed_from_previous, "config": cfg}
