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
_OID_ENT_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13.1"   # entPhysicalModelName.1
_OID_ENT_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11.1"  # entPhysicalSerialNum.1
_ENRICH_OIDS = [_OID_SYS_DESCR, _OID_SYS_OBJID, _OID_SYS_NAME, _OID_ENT_MODEL, _OID_ENT_SERIAL]

# entPhysicalTable column bases (for walks). The chassis row is at index .1 on
# Cisco, but AOS-CX puts it at a vendor index (e.g. 112001) — so the scalar
# .1 GET above comes back empty and we fall back to walking the column and
# taking the first real value.
_OID_ENT_MODEL_TBL = "1.3.6.1.2.1.47.1.1.1.1.13"   # entPhysicalModelName
_OID_ENT_SERIAL_TBL = "1.3.6.1.2.1.47.1.1.1.1.11"  # entPhysicalSerialNum
_OID_ENT_DESCR_TBL = "1.3.6.1.2.1.47.1.1.1.1.2"    # entPhysicalDescr
# SonicWall doesn't populate entPhysicalSerialNum; serial lives in its own MIB.
_OID_SONICWALL_SERIAL = "1.3.6.1.4.1.8741.1.3.1.1.0"  # snwlSysSerialNumber

# sysObjectID → model fallback when sysDescr can't name the model.
SYSOBJID_MODELS = {
    "1.3.6.1.4.1.9.1.2862": "C8000V",
    "1.3.6.1.4.1.9.1.1745": "CSR1000V",
    "1.3.6.1.4.1.9.1.516": "Catalyst 3750",
}

_VERSION_RE = re.compile(r"Version\s+([\d.]+[\w.()-]*)", re.IGNORECASE)
_MODEL_RE = re.compile(r"\b(C\d{4}V|ASR\d{3,4}|ISR\d{3,4}|WS-C\S+|N\d{4}|C\d{3,4})\b", re.IGNORECASE)
# AOS-CX sysDescr lexicon (e.g. "ArubaOS-CX 10.10.1010 ... Aruba6300M ...").
_AOS_CX_VERSION_RE = re.compile(r"ArubaOS-CX\s+([\d.]+)", re.IGNORECASE)
_AOS_CX_MODEL_RE = re.compile(r"(Aruba[\w-]+\d+[A-Z]?)")
# AOS-CX firmware token, e.g. "... Sw PL.10.16.1030" / "FL.10.10.1010".
_AOS_CX_FW_RE = re.compile(r"\b([A-Z]{2}\.\d[\d.]+)")
# Drops the trailing firmware token from a sysDescr ("… Sw PL.10.16.1030").
_AOS_CX_FW_SPLIT_RE = re.compile(r"\s+[A-Z]{2}\.\d")

# SonicWall sysDescr: "SonicWALL {model} (… SonicOS{X} {version})". Handles both
# SonicOS 7 (TZ/NSa, no X suffix) and SonicOSX 8 (NSv/NSsp):
#   v7: "SonicWALL TZ 670 (SonicOS Enhanced SonicOS 7.3.2-7010-R9118)"
#   v8: "SonicWALL NSv XS (SonicOS Enhanced SonicOSX 8.2.1-8010-R9437)"
# The lazy .*? skips the "SonicOS Enhanced" prefix and stops on the LAST
# SonicOS/SonicOSX token (the one carrying the version), and os_version is the
# bare version string without the SonicOS[X] word.
_SONICWALL_RE = re.compile(
    r"SonicWALL\s+(.+?)\s+\(.*?SonicOS[X]?\s+(\S+?)\)", re.IGNORECASE)


def _parse_sonicwall_descr(descr: str) -> dict:
    """
    Parse a SonicWall sysDescr into {model, os_version}. Returns {} for any
    non-SonicWall sysDescr. Examples:
      'SonicWALL TZ 670 (SonicOS Enhanced SonicOS 7.3.2-7010-R9118)'
        → {'model': 'TZ 670', 'os_version': '7.3.2-7010-R9118'}
      'SonicWALL NSv XS (SonicOS Enhanced SonicOSX 8.2.1-8010-R9437)'
        → {'model': 'NSv XS', 'os_version': '8.2.1-8010-R9437'}
    """
    m = _SONICWALL_RE.search(descr)
    if not m:
        return {}
    return {"model": m.group(1).strip(),
            "os_version": m.group(2).strip()}


def _model_from_aos_cx_descr(descr: str) -> str:
    """
    Extract the model from the HPE/ANW-prefixed AOS-CX sysDescr form, e.g.
    'HPE ANW R9Y04A 6100 48G CL4 4SFP+ Sw PL.10.16.1030'
        → 'R9Y04A 6100 48G CL4 4SFP+ Sw'
    Returns '' for any other sysDescr (so it never touches Cisco/Aruba-mobility).
    """
    if not re.search(r"\bHPE\b|\bANW\b", descr, re.IGNORECASE):
        return ""
    head = _AOS_CX_FW_SPLIT_RE.split(descr, 1)[0]          # drop firmware suffix
    head = re.sub(r"^\s*HPE\s+(?:ANW\s+)?", "", head, flags=re.IGNORECASE)
    return head.strip()
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


async def _snmp_walk_first(ip: str, base_oid: str, auth_data) -> str:
    """Walk an entPhysicalTable column and return its first real value ('' if none).

    A fresh SnmpEngine per walk so SNMPv3 engine-ID discovery runs cleanly.
    """
    from pysnmp.hlapi.v3arch.asyncio import (
        ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, walk_cmd,
    )
    target = await UdpTransportTarget.create((ip, 161), timeout=2.5, retries=1)
    async for err_ind, err_stat, _idx, var_binds in walk_cmd(
        SnmpEngine(), auth_data, target, ContextData(),
        ObjectType(ObjectIdentity(base_oid)),
        lexicographicMode=False,   # stop at the end of this subtree
    ):
        if err_ind or err_stat:
            break
        for vb in var_binds:
            val = _clean(str(vb[1]))
            if val:
                logger.info("SNMP walk matched %s = %s", _oid_display_name(str(vb[0])), val)
                return val
    return ""


def _oid_display_name(oid: str) -> str:
    """Human-readable MIB name for a numeric OID ("entPhysicalSerialNum.101001"),
    via the apps.mibs index. Falls back to the raw OID. Display/logging only."""
    try:
        from apps.mibs.index import resolve_oid
        return resolve_oid(oid).get("name") or oid
    except Exception:  # noqa: BLE001 — resolution is cosmetic, never break enrichment
        return oid


def _snmp_collect(ip: str, profile, secrets) -> dict:
    """Run the SNMP GET (sync wrapper). Returns {oid: value} or {}.

    Falls back to walking the entPhysical model/serial columns when the scalar
    ``.1`` GET is empty — AOS-CX indexes the chassis at e.g. 112001, not .1.
    """
    if not (profile.snmpv3_enabled or profile.snmpv2c_enabled):
        return {}
    try:
        from apps.credentials.snmp_auth import build_snmp_auth
        res = asyncio.run(_snmp_get(ip, _ENRICH_OIDS, build_snmp_auth(profile, secrets)))
        if _clean(res.get(_OID_ENT_SERIAL)) == "":
            serial = asyncio.run(_snmp_walk_first(ip, _OID_ENT_SERIAL_TBL, build_snmp_auth(profile, secrets)))
            if not serial:
                # SonicWall (and some others) don't fill entPhysicalSerialNum —
                # try the SonicWall serial scalar. Harmless on other vendors.
                got = asyncio.run(_snmp_get(ip, [_OID_SONICWALL_SERIAL], build_snmp_auth(profile, secrets)))
                serial = _clean(got.get(_OID_SONICWALL_SERIAL))
            if serial:
                res[_OID_ENT_SERIAL] = serial
        if _clean(res.get(_OID_ENT_MODEL)) == "":
            model = asyncio.run(_snmp_walk_first(ip, _OID_ENT_MODEL_TBL, build_snmp_auth(profile, secrets)))
            if not model:  # last resort: entPhysicalDescr of the first real entry
                model = asyncio.run(_snmp_walk_first(ip, _OID_ENT_DESCR_TBL, build_snmp_auth(profile, secrets)))
            if model:
                res[_OID_ENT_MODEL] = model
        return res
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("SNMP enrichment failed for %s: %s", ip, exc)
        return {}


def _parse_snmp(res: dict, updates: dict) -> None:
    from .management.commands.run_discovery import (
        _platform_from_descr, _platform_from_sysobjid, _vendor_from_descr, _vendor_from_sysobjid,
    )

    descr = _clean(res.get(_OID_SYS_DESCR))
    objid = _clean(res.get(_OID_SYS_OBJID))
    model = _clean(res.get(_OID_ENT_MODEL))
    serial = _clean(res.get(_OID_ENT_SERIAL))

    if descr:
        # SonicWall: "SonicWALL NSv XS (SonicOS Enhanced SonicOSX 8.2.1-…)".
        # Fill model + os_version here (the version regexes below don't match
        # SonicOS); vendor/platform fall to the shared detectors → "sonicwall",
        # keeping vendor lowercase like every other platform.
        sonic = _parse_sonicwall_descr(descr)
        if sonic:
            model = sonic["model"]
            updates["os_version"] = sonic["os_version"]
        # The HPE/ANW AOS-CX sysDescr carries the most descriptive model
        # ("HPE ANW R9Y04A 6100 …"); prefer it over the entPhysical column.
        descr_model = _model_from_aos_cx_descr(descr)
        if descr_model:
            model = descr_model
        # AOS-CX names its version differently: "ArubaOS-CX 10.10.1010" or, on
        # the 6100/6300, only a trailing firmware token ("… Sw PL.10.16.1030").
        m = (_AOS_CX_VERSION_RE.search(descr) or _VERSION_RE.search(descr)
             or _AOS_CX_FW_RE.search(descr))
        if m:
            updates["os_version"] = m.group(1)
        plat = _platform_from_descr(descr) or _platform_from_sysobjid(objid)
        if plat:
            updates["platform"] = plat
        ven = _vendor_from_sysobjid(objid) or _vendor_from_descr(descr)
        if ven:
            updates["vendor"] = ven
        if not model:
            mm = _MODEL_RE.search(descr) or _AOS_CX_MODEL_RE.search(descr)
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


# ── AOS-CX REST API (preferred for aos_cx) ──────────────────────────────────────

def _aos_cx_collect(ip: str, profile, secrets) -> dict:
    """
    Collect AOS-CX system info over the REST API. Reuses the SSH credentials
    (same username/password works for REST on AOS-CX). Returns the normalized
    ``get_system()`` dict, or ``{}`` on any failure (caller falls back to SNMP).
    """
    username = profile.ssh_username or secrets.get("ssh_username", "")
    password = secrets.get("ssh_password", "")
    if not (username and password):
        logger.info("AOS-CX REST enrichment for %s: no SSH credentials to reuse", ip)
        return {}
    try:
        from .aos_cx_client import AOSCXClient
        with AOSCXClient(ip) as client:
            client.login(username, password)
            # get_system_info() additionally resolves the serial number and base
            # MAC off the chassis subsystem (the bare get_system() can't see them).
            return client.get_system_info()
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("AOS-CX REST enrichment failed for %s: %s", ip, exc)
        return {}


def _parse_aos_cx(info: dict, updates: dict) -> None:
    """Map a normalized AOS-CX ``get_system_info()`` dict onto device-field
    updates. Accepts the older ``get_system()`` keys too (``version``/``serial``)
    so either source works. ``product_name`` (the chassis SKU description, e.g.
    "6300M 24SFP+ 4SFP56 Swch") is preferred for the model over the terse
    ``platform_name`` ("6300")."""
    if not info:
        return
    hostname = _clean(info.get("hostname"))
    version = _clean(info.get("os_version") or info.get("version"))
    model = _clean(info.get("product_name") or info.get("model"))
    serial = _clean(info.get("serial_number") or info.get("serial"))
    if hostname:
        updates["hostname"] = hostname
    if version:
        updates["os_version"] = version
    if model:
        updates["model"] = model
    if serial:
        updates["serial_number"] = serial
    # Platform/vendor are known once we've reached an AOS-CX device.
    updates.setdefault("platform", "aos_cx")
    updates.setdefault("vendor", "aruba")


# ── SonicWall REST API (preferred for sonicwall) ────────────────────────────────

def _sonicwall_collect(ip: str, profile, secrets) -> dict:
    """
    Collect SonicWall system info over the SonicOS REST API (preferred over
    SNMP). Prefers the HTTPS/API credential, falls back to SSH. Returns
    {model, os_version, serial, hostname} or {} on any failure (caller falls
    back to SNMP).
    """
    try:
        from apps.compliance.sonicwall_client import (
            SonicWallClient, resolve_rest_credentials,
        )
        username, password, port = resolve_rest_credentials(profile, secrets)
        if not password:
            return {}
        # SonicWall management certs are self-signed → don't verify TLS.
        with SonicWallClient(ip, username, password, port=port, verify_ssl=False) as client:
            cfg = client.get_config()
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("SonicWall REST enrichment failed for %s: %s", ip, exc)
        return {}
    admin = cfg.get("administration") or {}
    return {
        "model": _clean(cfg.get("model")),
        "os_version": _clean(cfg.get("firmware_version")),
        "serial": _clean(cfg.get("serial_number")),
        "hostname": _clean(admin.get("firewall_name")),
    }


def _parse_sonicwall_rest(info: dict, updates: dict) -> None:
    """Map the SonicWall REST config (config/current) onto device-field updates."""
    if not info:
        return
    if info.get("hostname"):
        updates["hostname"] = info["hostname"]
    if info.get("os_version"):
        updates["os_version"] = info["os_version"]
    if info.get("model"):
        updates["model"] = info["model"]
    if info.get("serial"):
        updates["serial_number"] = info["serial"]
    updates.setdefault("platform", "sonicwall")
    updates.setdefault("vendor", "sonicwall")


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

    # ── Step 1: device-info enrichment (REST → SNMP → SSH) ─────────────────────
    updates: dict = {}

    # AOS-CX / SonicWall: try the REST API first (most accurate). SNMP only runs
    # as a fallback when REST returns nothing, so it can't clobber REST values.
    aos_info: dict = {}
    sonic_info: dict = {}
    if device.platform == "aos_cx":
        aos_info = _aos_cx_collect(ip, profile, secrets)
        _parse_aos_cx(aos_info, updates)
    elif device.platform == "sonicwall":
        sonic_info = _sonicwall_collect(ip, profile, secrets)
        _parse_sonicwall_rest(sonic_info, updates)

    if device.platform not in ("aos_cx", "sonicwall") or not (aos_info or sonic_info):
        _parse_snmp(_snmp_collect(ip, profile, secrets), updates)

    # If SNMP just revealed this is an AOS-CX box that was misclassified on add
    # (e.g. stored as "other"), run the REST collector now so a single re-run —
    # not two — fills model/version/serial from the preferred source.
    if device.platform != "aos_cx" and updates.get("platform") == "aos_cx" and not aos_info:
        aos_info = _aos_cx_collect(ip, profile, secrets)
        _parse_aos_cx(aos_info, updates)

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

    # Verify the hostname against the network (SNMP sysName / DNS) and update it
    # if it changed — stamps hostname_verified_at, raises an info alert on change,
    # and re-applies hostname rules. Best-effort.
    try:
        from .hostname_check import check_and_update_hostname
        check_and_update_hostname(device)
    except Exception as exc:  # noqa: BLE001 — never block enrichment
        logger.warning("Hostname verification failed for %s: %s", device.hostname, exc)

    # Auto-assign role/site from hostname rules now that the hostname is known
    # (enrichment may have corrected it, e.g. AOS-CX/SonicWall REST). Best-effort:
    # only fills an unset role/site, never overrides an existing assignment.
    try:
        from .hostname_rules import apply_hostname_rules
        apply_hostname_rules(device)
    except Exception as exc:  # noqa: BLE001 — never block enrichment
        logger.warning("Hostname rule apply failed for %s: %s", device.hostname, exc)

    # ── Step 2: interface discovery ───────────────────────────────────────────
    interfaces = None
    try:
        interfaces, _found, _enabled = _discover_interfaces(device)
        # bulk_create skips the MonitoredInterface post_save signal that
        # republishes the device to the poller, and there's no periodic
        # republish — so without this nudge the new interfaces are persisted
        # but never polled.
        try:
            from . import snmp_publish
            snmp_publish.publish_device_upsert(device)
        except Exception as exc:  # noqa: BLE001 — best-effort republish
            logger.warning("Poller republish after interface discovery failed for %s: %s",
                           device.hostname, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Interface discovery failed for %s: %s", device.hostname, exc)

    # ── Step 3: LLDP neighbour discovery ──────────────────────────────────────
    try:
        links = _discover_lldp(device, interfaces)
        if links:
            _publish_topology_updated(device.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLDP discovery failed for %s: %s", device.hostname, exc)

    # ── Step 4: initial config collection ─────────────────────────────────────
    # Capture a baseline running-config immediately so drift detection works from
    # day one (don't wait for the next scheduled collection window).
    try:
        _collect_config(device)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Config collection failed for %s: %s", device.hostname, exc)

    logger.info("Enrichment complete for %s", device.hostname)
    return {f: getattr(device, f) for f in changed}


def _collect_config(device) -> None:
    """Collect and store the device's running config (initial baseline)."""
    from apps.configbackup.tasks import collect_device_config

    logger.info("Collecting initial config for %s", device.hostname)
    collect_device_config(device.id, collected_by="enrichment")


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
