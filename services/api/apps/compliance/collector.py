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


def store_config(device, content: str, collected_by: str) -> DeviceConfig:
    """Persist a running-config snapshot with hash + change detection."""
    content_hash = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
    prev = (
        DeviceConfig.objects
        .filter(device=device, config_type=DeviceConfig.ConfigType.RUNNING)
        .order_by("-collected_at")
        .first()
    )
    changed = prev is not None and prev.content_hash != content_hash
    diff_summary = None
    if changed:
        diff = difflib.unified_diff(
            prev.content.splitlines(), content.splitlines(),
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
        content=content,
        content_hash=content_hash,
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
        settings.NATS_URL,
        user=getattr(settings, "NATS_USER", None) or None,
        password=getattr(settings, "NATS_PASSWORD", None) or None,
    )
    try:
        await nc.publish(f"netpulse.config.{device_id}.collected", b"{}")
    finally:
        await nc.drain()


def collect_one(device, collected_by: str = "scheduled") -> DeviceConfig | None:
    """
    Collect and store one device's running config. Returns the DeviceConfig, or
    None on failure. Never raises — single-device failures must not stop the loop.
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
        elif "Timeout" in name or "timeout" in str(exc).lower():
            logger.warning("timeout collecting %s: %s", device.hostname, exc)
        else:
            logger.error("error collecting %s: %s", device.hostname, exc)
        return None

    if not content or not content.strip():
        logger.warning("empty config returned for %s — skipping", device.hostname)
        return None

    cfg = store_config(device, content, collected_by)
    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])
    publish_collected(device.id)

    elapsed = time.monotonic() - start
    logger.info(
        "collected %s (%s) — %d bytes, changed=%s, %.2fs",
        device.hostname, device.platform or "?", len(content), cfg.changed_from_previous, elapsed,
    )
    return cfg
