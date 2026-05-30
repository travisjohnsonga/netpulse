"""
Publish device SNMP configuration to NATS for the ingest-snmp poller.

The poller subscribes to ``netpulse.devices.upsert`` / ``netpulse.devices.remove``
and polls whatever it's told to. Here (the API) we build the **non-secret**
device config from the DB and publish it. Secrets (auth/priv keys, community)
are NEVER in the payload — only the OpenBao ``cred_path`` (vault_path); the
poller fetches the key material from OpenBao directly using that path.

Payload (matches ingest-snmp ingest/models.py Device.from_dict):
  device_id, hostname, ip, port, version (1/2/3), cred_path,
  snmp_username, snmp_auth_protocol, snmp_priv_protocol, snmp_security_level,
  poll_interval, poll_oids, interfaces[{if_name, if_index, poll_traffic,
  poll_errors, poll_status}]
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

UPSERT_SUBJECT = "netpulse.devices.upsert"
REMOVE_SUBJECT = "netpulse.devices.remove"

# Device-level OIDs polled for every SNMP device (sysUpTime + Cisco CPU/memory).
DEVICE_OIDS = [
    "1.3.6.1.2.1.1.3.0",                 # sysUpTime
    "1.3.6.1.4.1.9.9.109.1.1.1.1.8.1",   # cpmCPUTotal5min (Cisco)
    "1.3.6.1.4.1.9.9.48.1.1.1.5.1",      # ciscoMemoryPoolUsed
    "1.3.6.1.4.1.9.9.48.1.1.1.6.1",      # ciscoMemoryPoolFree
]
# Per-interface OID prefixes (suffixed with the interface ifIndex).
IFACE_OID_PREFIXES = {
    "status":  ["1.3.6.1.2.1.2.2.1.8"],                       # ifOperStatus
    "traffic": ["1.3.6.1.2.1.31.1.1.1.6", "1.3.6.1.2.1.31.1.1.1.10"],  # ifHCIn/OutOctets
    "errors":  ["1.3.6.1.2.1.2.2.1.14", "1.3.6.1.2.1.2.2.1.20"],       # ifIn/OutErrors
}


def _snmp_profile(device):
    """Return the device's credential profile if it's SNMP-capable, else None."""
    p = device.credential_profile
    if p and (p.snmpv3_enabled or p.snmpv2c_enabled):
        return p
    return None


def _poll_interval(device) -> int:
    cfg = getattr(device, "telemetry_config", None)
    if cfg and cfg.override_intervals and cfg.device_metrics_interval:
        return cfg.device_metrics_interval
    if cfg and cfg.snmp_interval:
        return cfg.snmp_interval
    return 300


def build_device_payload(device) -> dict | None:
    """
    Build the non-secret SNMP config payload for a device, or None when the
    device isn't pollable (inactive, or no SNMP credential profile).
    """
    if device.status != device.Status.ACTIVE:
        return None
    profile = _snmp_profile(device)
    if not profile:
        return None

    version = 3 if profile.snmpv3_enabled else 2
    port = (profile.snmpv3_port if version == 3 else profile.snmpv2c_port) or 161

    interfaces, oids = [], list(DEVICE_OIDS)
    for iface in device.monitored_interfaces.all():
        interfaces.append({
            "if_name": iface.if_name,
            "if_index": iface.if_index,
            "poll_traffic": iface.poll_traffic,
            "poll_errors": iface.poll_errors,
            "poll_status": iface.poll_status,
        })
        if iface.if_index is None:
            continue
        if iface.poll_status:
            oids += [f"{p}.{iface.if_index}" for p in IFACE_OID_PREFIXES["status"]]
        if iface.poll_traffic:
            oids += [f"{p}.{iface.if_index}" for p in IFACE_OID_PREFIXES["traffic"]]
        if iface.poll_errors:
            oids += [f"{p}.{iface.if_index}" for p in IFACE_OID_PREFIXES["errors"]]

    return {
        "device_id": str(device.id),
        "hostname": device.hostname,
        "ip": str(device.management_ip or device.ip_address),
        "port": port,
        "version": version,
        "cred_path": profile.vault_path or "",
        "snmp_username": profile.snmpv3_username or "",
        "snmp_auth_protocol": profile.snmpv3_auth_protocol or "SHA",
        "snmp_priv_protocol": profile.snmpv3_priv_protocol or "AES",
        "snmp_security_level": profile.snmpv3_security_level or "",
        "poll_interval": _poll_interval(device),
        "poll_oids": oids,
        "interfaces": interfaces,
    }


# ── NATS plumbing ─────────────────────────────────────────────────────────────

def _enabled() -> bool:
    return bool(getattr(settings, "SNMP_DEVICE_PUBLISH", True))


async def _connect():
    import nats  # lazy
    return await nats.connect(
        os.environ.get("NATS_URL", getattr(settings, "NATS_URL", "nats://nats:4222")),
        user=os.environ.get("NATS_USER", getattr(settings, "NATS_USER", "")) or None,
        password=os.environ.get("NATS_PASSWORD", getattr(settings, "NATS_PASSWORD", "")) or None,
        connect_timeout=3,
    )


async def _publish_many(messages: list[tuple[str, dict]]) -> None:
    nc = await _connect()
    try:
        for subject, payload in messages:
            await nc.publish(subject, json.dumps(payload).encode())
        await nc.flush()
    finally:
        await nc.drain()


def _run(messages: list[tuple[str, dict]]) -> bool:
    """Best-effort publish; returns True on success, False (logged) on failure."""
    if not messages:
        return True
    try:
        asyncio.run(_publish_many(messages))
        return True
    except Exception as exc:  # NATS down, etc. — never break the request.
        logger.warning("NATS device publish failed (%d msg): %s", len(messages), exc)
        return False


def publish_device_upsert(device) -> None:
    """Publish one device's config (best-effort). Removes it if not pollable."""
    if not _enabled():
        return
    payload = build_device_payload(device)
    if payload is None:
        publish_device_remove(device.id)
        return
    _run([(UPSERT_SUBJECT, payload)])


def publish_device_remove(device_id) -> None:
    if not _enabled():
        return
    _run([(REMOVE_SUBJECT, {"device_id": str(device_id)})])


def publish_all_active() -> int:
    """Publish every pollable device. Returns the count published."""
    from .models import Device
    messages = []
    qs = (Device.objects.filter(status=Device.Status.ACTIVE)
          .select_related("credential_profile", "telemetry_config")
          .prefetch_related("monitored_interfaces"))
    for device in qs:
        payload = build_device_payload(device)
        if payload is not None:
            messages.append((UPSERT_SUBJECT, payload))
    ok = _run(messages)
    return len(messages) if ok else 0
