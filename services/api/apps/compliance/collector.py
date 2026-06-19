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
import threading
import time

from django.conf import settings
from django.utils import timezone

from apps.configbackup.models import ConfigBackupSettings, ConfigCollectionLog, DeviceConfig
from apps.credentials import vault

logger = logging.getLogger(__name__)

# Platforms whose configuration is owned by a vendor controller/cloud (UniFi,
# Mist) or is unknown/unset — there is no SSH/REST running-config to collect, so
# attempting collection only produces noise and false failures. Skipped on every
# path (scheduled, manual, enrichment). Wireless APs/controllers also match by
# role (see collect_all_configs).
SKIP_CONFIG_PLATFORMS = frozenset({
    "unifi_ap", "unifi_udm", "unifi_sw", "unifi_uckp", "unifi_ucg",
    "mist_ap", "mist_sw", "mist_gw",
    "unknown", "",
})


def config_collection_supported(device) -> bool:
    """True when ``device``'s platform can have a running config collected."""
    return (getattr(device, "platform", "") or "").lower() not in SKIP_CONFIG_PLATFORMS


# The transport actually used to fetch the last config is recorded here by the
# fetch path and consumed once by collect_one when it writes the attempt log.
# Thread-local so concurrent collections don't clobber each other's value.
_collection_ctx = threading.local()


def _record_method(method: str) -> None:
    _collection_ctx.method = method


def _consume_method() -> str:
    method = getattr(_collection_ctx, "method", "")
    _collection_ctx.method = ""
    return method

# NetPulse platform → Netmiko device_type.
_NETMIKO_TYPES = {
    "ios": "cisco_ios",
    "ios_xe": "cisco_xe",
    "ios_xr": "cisco_xr",
    "nxos": "cisco_nxos",
    "eos": "arista_eos",
    "junos": "juniper_junos",
    "sonic": "linux",
    # SonicWall firewalls. Netmiko 4.x has no native SonicOS driver (sonic_os
    # does not exist), so use the generic SSH driver — pinning a device_type
    # still beats autodetect, which mis-IDs SonicOS as cisco_ios.
    "sonicwall": "generic",
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


def collect_sonicwall_config(device, profile, creds: dict) -> str:
    """Collect a SonicWall's full config as pretty JSON via the SonicOS REST API."""
    import json

    from .sonicwall_client import SonicWallClient, resolve_rest_credentials
    username, password, port = resolve_rest_credentials(profile, creds)
    # SonicWall management certs are self-signed → don't verify TLS.
    with SonicWallClient(str(device.management_ip or device.ip_address),
                         username, password, port=port, verify_ssl=False) as client:
        return json.dumps(client.get_config(), indent=2)


def _fetch_aos_cx_via_rest(device, profile, creds: dict) -> str:
    """Collect an AOS-CX running config via the REST API (fastest path)."""
    import json

    from apps.devices.aos_cx_client import AOSCXClient

    from apps.devices.aos_cx_render import aos_cx_json_to_cli

    username = (profile.ssh_username if profile else "") or creds.get("ssh_username", "")
    password = creds.get("ssh_password", "")
    with AOSCXClient(device_host(device)) as client:
        client.login(username, password)
        data = client.get_running_config()
    # The REST endpoint returns the config as a JSON document. Convert it to CLI
    # text BEFORE storing so backups display and diff like every other platform
    # (raw JSON is unreadable and diffs noisily on key ordering). Fall back to a
    # stable pretty-printed JSON only if the CLI render comes back empty.
    if isinstance(data, dict):
        cli = aos_cx_json_to_cli(data)
        if cli:
            return cli
    return json.dumps(data, indent=2, sort_keys=True) if isinstance(data, (dict, list)) else str(data)


def _fetch_aos_cx_via_ssh(device, profile, creds: dict) -> str:
    """
    Collect an AOS-CX running config over SSH using a non-interactive
    ``exec_command`` channel (paramiko directly).

    Netmiko's ``send_command`` drives the interactive shell, which on AOS-CX
    paginates ``show running-config`` behind a ``--More--`` pager that Netmiko
    doesn't handle for this platform — the call then blocks until the read
    timeout. The exec channel returns the whole config at once with no pager, so
    we bypass Netmiko entirely here.
    """
    import paramiko  # lazy

    host = device_host(device)
    username = (profile.ssh_username if profile else "") or creds.get("ssh_username", "")
    password = creds.get("ssh_password", "")
    port = (profile.ssh_port if profile else 22) or 22

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            host, port=port, username=username, password=password,
            timeout=15, look_for_keys=False, allow_agent=False,
        )
        _stdin, stdout, _stderr = ssh.exec_command("show running-config", timeout=60)
        config = stdout.read().decode("utf-8", errors="replace")
        if not config.strip():
            raise ValueError("empty config returned")
        return _strip_aos_cx_preamble(config)
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001 — close is best-effort
            pass


def _strip_aos_cx_preamble(config: str) -> str:
    """Drop the non-config preamble AOS-CX prints before the running config
    (e.g. ``Current configuration:``); keep from the first real config line so
    the stored text begins at ``!``/``hostname`` and diffs stay stable."""
    lines = config.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("hostname", "!", "vlan", "interface")):
            return "\n".join(lines[i:])
    return config


def collect_aos_cx_config(device, profile, creds: dict) -> str:
    """
    Collect an AOS-CX switch's running config.

    Prefers SSH ``show running-config`` over a paramiko ``exec_command`` channel:
    it returns the COMPLETE running config (ntp/radius/aaa/snmp/routes/logging/
    banner/spanning-tree/vrf/…) in one call as native CLI text. Falls back to the
    REST API, which only yields a partial config (vlans + interfaces, rendered to
    CLI). Netmiko is deliberately avoided — its interactive ``send_command`` hangs
    on the AOS-CX ``--More--`` pager; the exec channel is pager-immune.
    """
    try:
        config = _fetch_aos_cx_via_ssh(device, profile, creds)
        _record_method("ssh")
        return config
    except Exception as exc:  # noqa: BLE001 — SSH unreachable → REST fallback
        logger.debug("AOS-CX SSH config failed for %s, falling back to REST: %s",
                     device.hostname, exc)
    config = _fetch_aos_cx_via_rest(device, profile, creds)
    _record_method("rest")
    return config


def _diff_counts(diff: str) -> tuple[int, int]:
    """(added, removed) line counts from a unified diff, ignoring the +++/--- headers."""
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return added, removed


def _config_text(data) -> str:
    """Normalise an AOS-CX config payload (JSON document or text) to comparable text."""
    import json
    if isinstance(data, (dict, list)):
        return json.dumps(data, indent=2, sort_keys=True)
    return str(data or "")


def _check_aos_cx_startup(device) -> dict:
    """Compare AOS-CX running vs startup config over REST."""
    from apps.devices.aos_cx_client import AOSCXClient

    profile = device.credential_profile
    if not profile:
        return {"checked": False, "match": None, "error": "no credentials"}
    creds = get_credentials(device)
    username = (profile.ssh_username or "") or creds.get("ssh_username", "")
    password = creds.get("ssh_password", "")
    try:
        with AOSCXClient(device_host(device)) as client:
            client.login(username, password)
            running = _config_text(client.get_running_config())
            startup = _config_text(client.get_startup_config())
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "match": None, "error": str(exc)[:300]}

    if running.strip() == startup.strip():
        return {"checked": True, "match": True, "diff": "", "method": "rest", "added": 0, "removed": 0}
    diff = "\n".join(difflib.unified_diff(
        startup.splitlines(), running.splitlines(),
        fromfile="startup-config", tofile="running-config", lineterm=""))
    added, removed = _diff_counts(diff)
    return {"checked": True, "match": False, "diff": diff, "method": "rest",
            "added": added, "removed": removed}


def _check_cisco_startup(device) -> dict:
    """Compare Cisco running vs startup via ``show archive config differences``."""
    from netmiko import ConnectHandler  # lazy

    profile = device.credential_profile
    if not profile:
        return {"checked": False, "match": None, "error": "no credentials"}
    creds = get_credentials(device)
    params = {
        "device_type": netmiko_device_type(device.vendor, device.platform),
        "host": device_host(device),
        "username": profile.ssh_username or "",
        "password": creds.get("ssh_password", ""),
        "port": (profile.ssh_port or 22) or 22,
        "fast_cli": False,
        "conn_timeout": 30,
    }
    if params["device_type"] == "autodetect":
        params["device_type"] = "cisco_ios"
    try:
        conn = ConnectHandler(**params)
        try:
            output = conn.send_command(
                "show archive config differences nvram:startup-config system:running-config",
                read_timeout=60)
        finally:
            conn.disconnect()
    except Exception as exc:  # noqa: BLE001
        return {"checked": False, "match": None, "error": str(exc)[:300]}

    text = output or ""
    if not text.strip() or "no differences" in text.lower():
        return {"checked": True, "match": True, "diff": "", "method": "ssh", "added": 0, "removed": 0}
    added, removed = _diff_counts(text)
    return {"checked": True, "match": False, "diff": text, "method": "ssh",
            "added": added, "removed": removed}


def check_running_startup_match(device) -> dict:
    """
    Compare a device's running config against its saved startup config.

    A mismatch means there are unsaved changes that will be lost on the next
    reboot — a common cause of post-outage incidents. Returns
    ``{checked, match, diff, method, error, added, removed}``; ``checked`` is
    False (and ``match`` None) for unsupported platforms or on any error, so the
    caller can treat it as "unknown" rather than a failure.
    """
    platform = (device.platform or "").lower()
    if platform == "aos_cx":
        return _check_aos_cx_startup(device)
    if platform in ("ios", "ios_xe"):
        return _check_cisco_startup(device)
    return {"checked": False, "match": None, "error": f"not supported for {platform or 'unknown'}"}


_CONFIG_UNSAVED_RULE_NAME = "Startup config not saved"


def _config_unsaved_rule():
    """Get/create the system AlertRule for running-vs-startup mismatches (WARNING)."""
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=_CONFIG_UNSAVED_RULE_NAME,
        defaults={
            "description": "Warns when a device's running config differs from its saved "
                           "startup config (unsaved changes lost on reboot).",
            "severity": AlertRule.Severity.MEDIUM,
            "condition": {"rule_type": "config_unsaved"},
            "cooldown_minutes": 0,
            "is_system": True,
        },
    )
    return rule


def _reconcile_startup_alert(device, result: dict) -> None:
    """Fire a standing WARNING on a mismatch; resolve it when configs match again."""
    from apps.alerts.models import AlertEvent

    if not result.get("checked"):
        return
    open_qs = AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING,
        labels__alert_type="config_unsaved",
        labels__device_id=device.id,
    )
    if result.get("match"):
        # Saved now — resolve any open mismatch alert.
        for ev in open_qs:
            ev.state = AlertEvent.State.RESOLVED
            ev.resolved_at = timezone.now()
            ev.resolution_note = "Running config now matches startup."
            ev.save(update_fields=["state", "resolved_at", "resolution_note"])
        return
    if open_qs.exists():
        return  # already firing — don't spam a new event each collection
    unsaved = (result.get("added", 0) or 0) + (result.get("removed", 0) or 0)
    AlertEvent.objects.create(
        rule=_config_unsaved_rule(),
        state=AlertEvent.State.FIRING,
        labels={
            "source": "config_backup", "device": device.hostname, "device_id": device.id,
            "severity": "warning", "alert_type": "config_unsaved",
        },
        annotations={
            "title": f"Startup config not saved: {device.hostname}",
            "message": (
                f"Running config on {device.hostname} has {unsaved} unsaved change(s). "
                f"These will be lost on reboot. Run 'write memory' to save."),
            "severity": "warning", "unsaved_lines": unsaved,
        },
    )


def update_startup_match(device, target_cfg) -> dict | None:
    """
    Run the running-vs-startup check and persist it onto ``target_cfg`` (the
    DeviceConfig to stamp — the just-stored snapshot, or the latest existing one
    when the config was unchanged). Fires/resolves the config_unsaved alert.
    Best-effort: returns the check result, or None when there's nothing to stamp.
    """
    if target_cfg is None:
        return None
    try:
        result = check_running_startup_match(device)
    except Exception as exc:  # noqa: BLE001 — startup check must not break collection
        logger.warning("startup check failed for %s: %s", device.hostname, exc)
        return None
    if not result.get("checked"):
        return result
    target_cfg.startup_match = bool(result.get("match"))
    target_cfg.startup_diff = result.get("diff", "") or ""
    target_cfg.startup_checked_at = timezone.now()
    target_cfg.save(update_fields=["startup_match", "startup_diff", "startup_checked_at"])
    if not result.get("match"):
        logger.warning("%s: running/startup mismatch — %d line(s) differ",
                       device.hostname, (result.get("added", 0) + result.get("removed", 0)))
    try:
        _reconcile_startup_alert(device, result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("startup alert reconcile failed for %s: %s", device.hostname, exc)
    return result


def _fetch_running_config(device, creds: dict) -> str:
    """
    Dispatch to the right protocol and return the running config text.

    SonicWall uses its REST API (SSH CLI is limited); AOS-CX uses REST then a
    non-interactive SSH exec channel (Netmiko hangs on its pager); SSH is primary
    for everything else; NETCONF is used when it's the only enabled protocol.
    This is the single network seam — tests monkeypatch it.
    """
    profile = device.credential_profile
    platform = (device.platform or "").lower()
    if platform == "sonicwall":
        _record_method("rest")
        return collect_sonicwall_config(device, profile, creds)
    if platform == "aos_cx":
        # collect_aos_cx_config records "rest" or "ssh" depending on which path won.
        return collect_aos_cx_config(device, profile, creds)
    if profile and profile.netconf_enabled and not profile.ssh_enabled:
        _record_method("netconf")
        return _fetch_via_netconf(device, profile, creds)
    _record_method("netmiko")
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

    Cloud/controller-managed platforms (UniFi/Mist APs, controllers, switches)
    and devices with no platform have no collectable config — they return
    ``{"ok": False, "error": "not_supported", "skipped": True}`` immediately and
    write NO ConfigCollectionLog row (so they don't pollute collection health).
    """
    if not config_collection_supported(device):
        logger.debug("config collection unsupported for %s (platform=%s) — skipping",
                     device.hostname, device.platform)
        return {"ok": False, "error": "not_supported", "skipped": True}

    start = time.monotonic()

    def _log(status, *, changed=None, content=None, error=""):
        """Write one ConfigCollectionLog row for this attempt (best-effort)."""
        try:
            ConfigCollectionLog.objects.create(
                device=device,
                status=status,
                collected_by=collected_by,
                duration_ms=int((time.monotonic() - start) * 1000),
                error_message=(error or "")[:512],
                config_changed=changed,
                bytes_collected=len(content) if content else None,
                method=_consume_method(),
            )
        except Exception as exc:  # noqa: BLE001 — logging must not break collection
            logger.warning("collection-log write failed for %s: %s", device.hostname, exc)

    creds = get_credentials(device)
    try:
        content = _fetch_running_config(device, creds)
    except Exception as exc:
        name = type(exc).__name__
        if "Auth" in name:
            _mark_credential_failed(device, f"{name}: authentication failed")
            logger.error("auth failure collecting %s (%s)", device.hostname, name)
            _log(ConfigCollectionLog.Status.AUTH_FAILED, error=f"{name}: authentication failed")
            return {"ok": False, "error": "auth_failed"}
        if "Timeout" in name or "timeout" in str(exc).lower():
            logger.warning("timeout collecting %s: %s", device.hostname, exc)
            _log(ConfigCollectionLog.Status.TIMEOUT, error=str(exc))
            return {"ok": False, "error": "timeout"}
        logger.error("error collecting %s: %s", device.hostname, exc)
        _log(ConfigCollectionLog.Status.FAILED, error=str(exc))
        return {"ok": False, "error": "error"}

    if not content or not content.strip():
        logger.warning("empty config returned for %s — skipping", device.hostname)
        _log(ConfigCollectionLog.Status.EMPTY, error="empty config returned")
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

    # Run template compliance against the new snapshot (best-effort; independent
    # of config-change — a freshly stored baseline should be evaluated too).
    if cfg is not None:
        try:
            from .engine import run_compliance_for_device
            run_compliance_for_device(device, config_snapshot=cfg)
        except Exception as exc:  # noqa: BLE001 — compliance must not break collection
            logger.warning("compliance run after collection failed for %s: %s", device.hostname, exc)

    # Reconcile running-vs-startup config (unsaved-changes detection). Stamp the
    # just-stored snapshot, or — when the config was unchanged — the latest
    # existing one, so the startup status on the newest snapshot stays current.
    target_cfg = cfg or (
        DeviceConfig.objects
        .filter(device=device, config_type=DeviceConfig.ConfigType.RUNNING)
        .order_by("-collected_at").first()
    )
    update_startup_match(device, target_cfg)

    elapsed = time.monotonic() - start
    if cfg is None:
        logger.info("config unchanged for %s (%s) — %.2fs", device.hostname, device.platform or "?", elapsed)
        _log(ConfigCollectionLog.Status.UNCHANGED, changed=False, content=content)
        return {"ok": True, "stored": False, "changed": False, "config": None}

    if cfg.changed_from_previous:
        logger.info("config changed, stored for %s (%s) — %d bytes, %.2fs",
                    device.hostname, device.platform or "?", len(content), elapsed)
    else:
        logger.info("config stored (initial baseline) for %s (%s) — %d bytes, %.2fs",
                    device.hostname, device.platform or "?", len(content), elapsed)
    _log(ConfigCollectionLog.Status.SUCCESS, changed=cfg.changed_from_previous, content=content)
    return {"ok": True, "stored": True, "changed": cfg.changed_from_previous, "config": cfg}
