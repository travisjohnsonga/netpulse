"""
Post-approval device enrichment.

After a discovered device is approved (or on demand via the /enrich/ endpoint)
we probe it over SNMP — then SSH as a fallback — to fill in model, OS version,
serial number, platform and vendor that the lightweight discovery scan couldn't
determine.

Runs in a background daemon thread (off the approve action / endpoint).
Best-effort: every failure is logged, never raised, and existing non-empty
device fields are never overwritten with blanks.
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# SNMP OIDs (the .1 instance for the chassis on entPhysicalTable).
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
_OID_SYS_OBJID = "1.3.6.1.2.1.1.2.0"
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
_OID_ENT_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13.1"   # entPhysicalModelName
_OID_ENT_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11.1"  # entPhysicalSerialNum
_ENRICH_OIDS = [_OID_SYS_DESCR, _OID_SYS_OBJID, _OID_SYS_NAME, _OID_ENT_MODEL, _OID_ENT_SERIAL]

# sysObjectID → model fallback when sysDescr can't name the model.
SYSOBJID_MODELS = {
    "1.3.6.1.4.1.9.1.2862": "C8000V",
    "1.3.6.1.4.1.9.1.1745": "CSR1000V",
    "1.3.6.1.4.1.9.1.516": "Catalyst 3750",
}

_VERSION_RE = re.compile(r"Version\s+([\d.]+[\w.()-]*)", re.IGNORECASE)
_MODEL_RE = re.compile(r"\b(C\d{4}V|ASR\d{3,4}|ISR\d{3,4}|WS-C\S+|N\d{4}|C\d{3,4})\b", re.IGNORECASE)
# SNMP "no value" sentinels that must not be saved.
_SNMP_NULLS = {"", "no such instance", "no such object", "nosuchinstance",
               "nosuchobject", "none", "null"}


def _clean(value) -> str:
    s = (value or "").strip()
    return "" if s.lower() in _SNMP_NULLS else s


# ── SNMP ──────────────────────────────────────────────────────────────────────

async def _snmp_get(ip: str, oids: list[str], auth_data) -> dict:
    from pysnmp.hlapi.v3arch.asyncio import (
        ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, get_cmd,
    )
    target = await UdpTransportTarget.create((ip, 161), timeout=2.5, retries=1)
    err_ind, err_stat, _idx, var_binds = await get_cmd(
        SnmpEngine(), auth_data, target, ContextData(),
        *[ObjectType(ObjectIdentity(o)) for o in oids],
    )
    if err_ind or err_stat:
        return {}
    return {str(vb[0]): str(vb[1]) for vb in var_binds}


def _build_auth(profile, secrets):
    """pysnmp auth object from the device's credential profile + OpenBao secrets."""
    from pysnmp.hlapi.v3arch.asyncio import CommunityData, UsmUserData

    if profile.snmpv3_enabled and profile.snmpv3_username:
        from pysnmp.hlapi.v3arch.asyncio import (
            usmAesCfb128Protocol, usmAesCfb192Protocol, usmAesCfb256Protocol,
            usmDESPrivProtocol, usmHMAC128SHA224AuthProtocol, usmHMAC192SHA256AuthProtocol,
            usmHMAC256SHA384AuthProtocol, usmHMAC384SHA512AuthProtocol,
            usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
        )
        auth_map = {
            "MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol,
            "SHA224": usmHMAC128SHA224AuthProtocol, "SHA256": usmHMAC192SHA256AuthProtocol,
            "SHA384": usmHMAC256SHA384AuthProtocol, "SHA512": usmHMAC384SHA512AuthProtocol,
        }
        priv_map = {
            "DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol,
            "AES128": usmAesCfb128Protocol, "AES192": usmAesCfb192Protocol,
            "AES256": usmAesCfb256Protocol,
        }
        auth_p = auth_map.get((profile.snmpv3_auth_protocol or "SHA").upper(), usmHMACSHAAuthProtocol)
        priv_p = priv_map.get((profile.snmpv3_priv_protocol or "AES").upper(), usmAesCfb128Protocol)
        level = profile.snmpv3_security_level or "authPriv"
        auth_key = secrets.get("snmpv3_auth_key") or None
        priv_key = secrets.get("snmpv3_priv_key") or None
        if level == "noAuthNoPriv":
            return UsmUserData(profile.snmpv3_username)
        if level == "authNoPriv" or not priv_key:
            return UsmUserData(profile.snmpv3_username, auth_key, authProtocol=auth_p)
        return UsmUserData(profile.snmpv3_username, auth_key, priv_key,
                           authProtocol=auth_p, privProtocol=priv_p)
    # SNMPv2c (community).
    return CommunityData(secrets.get("snmpv2c_community") or "public", mpModel=1)


def _snmp_collect(ip: str, profile, secrets) -> dict:
    """Run the SNMP GET (sync wrapper). Returns {oid: value} or {}."""
    if not (profile.snmpv3_enabled or profile.snmpv2c_enabled):
        return {}
    try:
        auth = _build_auth(profile, secrets)
        return asyncio.run(_snmp_get(ip, _ENRICH_OIDS, auth))
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("SNMP enrichment failed for %s: %s", ip, exc)
        return {}


def _parse_snmp(res: dict, updates: dict) -> None:
    from .management.commands.run_discovery import _platform_from_descr, _vendor_from_descr, _vendor_from_sysobjid

    descr = _clean(res.get(_OID_SYS_DESCR))
    objid = _clean(res.get(_OID_SYS_OBJID))
    model = _clean(res.get(_OID_ENT_MODEL))
    serial = _clean(res.get(_OID_ENT_SERIAL))

    if descr:
        m = _VERSION_RE.search(descr)
        if m:
            updates["os_version"] = m.group(1)
        plat = _platform_from_descr(descr)
        if plat:
            updates["platform"] = plat
        ven = _vendor_from_sysobjid(objid) or _vendor_from_descr(descr)
        if ven:
            updates["vendor"] = ven
        if not model:
            mm = _MODEL_RE.search(descr)
            if mm:
                model = mm.group(1)
    if not model and objid:
        model = SYSOBJID_MODELS.get(objid, "")
    if model:
        updates["model"] = model
    if serial:
        updates["serial_number"] = serial


# ── SSH fallback ──────────────────────────────────────────────────────────────

def _ssh_collect(ip: str, profile, secrets) -> dict:
    """SSH login + show-version via Netmiko (apps.devices.detect). {} on failure."""
    if not (profile.ssh_enabled and profile.ssh_username):
        return {}
    try:
        from . import detect
        det = detect.detect_platform(
            ip, profile.ssh_username, secrets.get("ssh_password", ""), profile.ssh_port or 22)
    except Exception as exc:  # noqa: BLE001
        logger.warning("SSH enrichment failed for %s: %s", ip, exc)
        return {}
    return det if det and det.get("detected") else {}


def _merge_ssh(det: dict, updates: dict) -> None:
    for src, field in (("platform", "platform"), ("os_version", "os_version"),
                       ("model", "model"), ("serial", "serial_number"), ("vendor", "vendor")):
        val = _clean(det.get(src))
        if val and field not in updates:
            updates[field] = val


# ── orchestration ─────────────────────────────────────────────────────────────

def _discover_interfaces(device):
    """
    Discover interfaces and persist the LLDP-connected (auto-selected) ones as
    MonitoredInterface rows (only when the device has none yet, so re-enrichment
    doesn't clobber a manual selection). Returns (interfaces, found, enabled).
    """
    from django.utils import timezone

    from apps.telemetry import discovery
    from apps.telemetry.models import MonitoredInterface

    interfaces = discovery.discover_interfaces(device)
    auto = [i for i in interfaces if i.get("auto_select")]
    enabled = 0
    if auto and not MonitoredInterface.objects.filter(device=device).exists():
        now = timezone.now()
        MonitoredInterface.objects.bulk_create([
            MonitoredInterface(
                device=device, if_index=i.get("if_index"), if_name=i["if_name"],
                if_description=i.get("if_description", "") or "",
                if_speed_mbps=i.get("if_speed_mbps"), if_type=i.get("if_type", "") or "",
                lldp_neighbor_hostname=i.get("lldp_neighbor_hostname"),
                lldp_neighbor_port=i.get("lldp_neighbor_port"),
                lldp_neighbor_desc=i.get("lldp_neighbor_desc"),
                poll_traffic=True, poll_errors=True, poll_status=True,
                collection_method=i.get("collection_method", "auto"),
                last_discovered=now, last_status=i.get("oper_status") or "unknown",
            )
            for i in auto
        ])
        enabled = len(auto)
    logger.info("Interface discovery: %d interfaces found, %d LLDP-enabled for %s",
                len(interfaces), enabled, device.hostname)
    return interfaces, len(interfaces), enabled


def _discover_lldp(device, interfaces=None) -> int:
    """Discover LLDP neighbours → TopologyLink rows. Returns links created."""
    from .topology import discover_links

    found = discover_links(device, interfaces=interfaces)
    matched = sum(1 for f in found if f.get("matched_device_id"))
    logger.info("LLDP discovery: %d neighbors found, %d topology links created for %s",
                len(found), matched, device.hostname)
    return matched


def _publish_topology_updated(device_id: int) -> None:
    """Best-effort WebSocket nudge so the topology map can reload its edges."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            "devices", {"type": "topology_updated", "payload": {"device_id": device_id}})
    except Exception as exc:  # noqa: BLE001
        logger.debug("topology_updated publish failed: %s", exc)


def enrich_device(device_id: int) -> dict:
    """
    Post-approval enrichment pipeline (best-effort, each step independent):
      1. SNMP, then SSH — fill model / os_version / serial / platform / vendor.
      2. Interface discovery — persist LLDP-connected interfaces for monitoring.
      3. LLDP neighbour discovery — create TopologyLink rows.
    A failure in any step is logged and never blocks the others. Returns the
    dict of device fields changed in step 1.
    """
    from .models import Device

    try:
        device = Device.objects.select_related("credential_profile").get(id=device_id)
    except Device.DoesNotExist:
        return {}

    profile = device.credential_profile
    if not profile:
        logger.info("enrich %s: no credential profile — skipping", device.hostname)
        return {}

    ip = str(device.management_ip or device.ip_address)
    secrets = {}
    try:
        from apps.credentials import vault
        if profile.vault_path:
            secrets = vault.read_secret(profile.vault_path) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrich %s: could not read secrets: %s", device.hostname, exc)

    # ── Step 1: SNMP/SSH device-info enrichment ───────────────────────────────
    updates: dict = {}
    _parse_snmp(_snmp_collect(ip, profile, secrets), updates)

    def missing(field):
        return field not in updates and not getattr(device, field, "")
    if missing("model") or missing("os_version") or missing("serial_number"):
        _merge_ssh(_ssh_collect(ip, profile, secrets), updates)

    changed = []
    for field, val in updates.items():
        # platform is corrected even if already set (ios → ios_xe).
        if val and getattr(device, field, "") != val:
            setattr(device, field, val)
            changed.append(field)
    if changed:
        device.save(update_fields=changed + ["updated_at"])
        logger.info("SNMP/SSH enrichment complete for %s: %s", device.hostname,
                    {f: getattr(device, f) for f in changed})

    # ── Step 2: interface discovery ───────────────────────────────────────────
    interfaces = None
    try:
        interfaces, _found, _enabled = _discover_interfaces(device)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Interface discovery failed for %s: %s", device.hostname, exc)

    # ── Step 3: LLDP neighbour discovery ──────────────────────────────────────
    try:
        links = _discover_lldp(device, interfaces)
        if links:
            _publish_topology_updated(device.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLDP discovery failed for %s: %s", device.hostname, exc)

    logger.info("Enrichment complete for %s", device.hostname)
    return {f: getattr(device, f) for f in changed}


def _enrich_worker(device_id: int) -> None:
    from django.db import connection
    try:
        enrich_device(device_id)
    finally:
        connection.close()


def trigger_enrich(device) -> bool:
    """
    Schedule enrichment in a daemon thread after the surrounding transaction
    commits (so the worker's separate connection sees the device). Gated by
    settings.DEVICE_AUTO_ENRICH (off in tests). Returns True when scheduled.
    """
    from django.conf import settings
    from django.db import transaction

    if not getattr(settings, "DEVICE_AUTO_ENRICH", True):
        return False
    if not device.credential_profile_id:
        return False
    from threading import Thread

    device_id = device.id
    transaction.on_commit(
        lambda: Thread(target=_enrich_worker, args=(device_id,), daemon=True).start())
    return True
