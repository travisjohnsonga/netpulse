"""
Import Juniper Mist cloud-managed devices into the NetPulse inventory.

A single org token lists the org's sites (upserted as MistSite rows) and each
site's devices (APs/switches/gateways), mapped to Device records keyed by stable
identity (MAC → IP → hostname+platform) so re-syncs update in place. Best-effort:
an unreachable site records the error and is skipped, never aborting the run.
"""
from __future__ import annotations

import ipaddress
import logging
import re

logger = logging.getLogger(__name__)

# Mist device 'type' → (NetPulse platform, DeviceRole slug).
MIST_TYPE_MAP = {
    "ap": ("mist_ap", "wireless-ap"),
    "switch": ("mist_sw", "access-switch"),
    "gateway": ("mist_gw", "router"),
}


def _valid_ip(value) -> str:
    try:
        ipaddress.ip_address(str(value))
        return str(value)
    except (ValueError, TypeError):
        return ""


def _normalize_mac(value) -> str:
    """Canonicalise a MAC to lowercase colon form, or '' if not a valid MAC."""
    hexes = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    if len(hexes) != 12:
        return ""
    hexes = hexes.lower()
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2))


def _unique_hostname(base: str) -> str:
    """Return a hostname not already taken (Device.hostname is unique)."""
    from apps.devices.models import Device
    name = base
    n = 1
    while Device.objects.filter(hostname=name).exists():
        n += 1
        name = f"{base}-{n}"
    return name


def _find_existing_device(mac: str, ip: str, hostname: str, platform: str):
    """Find the Device this Mist device already maps to, by stable identity.

    Priority: MAC → IP (management_ip/ip_address) → hostname+platform. Keying on
    MAC first stops a changed IP from spawning a duplicate ``<name>-2`` record.
    """
    from django.db.models import Q

    from apps.devices.models import Device

    if mac:
        d = Device.objects.filter(mac_address=mac).first()
        if d:
            return d
    if ip:
        d = Device.objects.filter(Q(management_ip=ip) | Q(ip_address=ip)).first()
        if d:
            return d
    if hostname:
        d = Device.objects.filter(hostname=hostname, platform=platform).first()
        if d:
            return d
    return None


def _import_device(raw: dict, mist_site) -> str:
    """Create/update a Device from one merged Mist device dict (inventory + stats).

    Returns 'imported' | 'updated' | 'skipped'. ``raw`` is expected to carry
    ``name``/``mac``/``model``/``type`` from the inventory and ``ip``/``version``/
    ``status`` merged in from the stats endpoint.
    """
    from django.utils import timezone

    from apps.devices.models import Device, DeviceRole

    ip = _valid_ip(raw.get("ip"))
    if not ip:
        return "skipped"  # ip_address is required+unique — can't create without one

    mac = _normalize_mac(raw.get("mac"))
    platform, role_slug = MIST_TYPE_MAP.get((raw.get("type") or "").lower(), ("other", None))
    role = DeviceRole.objects.filter(slug=role_slug).first() if role_slug else None
    name = (raw.get("name") or raw.get("mac") or ip).strip()
    model = (raw.get("model") or "").strip()
    version = (raw.get("version") or "").strip()
    reachable = str(raw.get("status") or "").lower() == "connected"
    site = mist_site.site if (mist_site and mist_site.site_id) else None

    existing = _find_existing_device(mac, ip, name, platform)
    if existing:
        existing.platform = platform
        existing.vendor = existing.vendor or "Juniper"
        if mac and existing.mac_address != mac:
            existing.mac_address = mac
        if model:
            existing.model = model
        if version:
            existing.os_version = version
        if role and not existing.role_id:
            existing.role = role
        if site and not existing.site_id:
            existing.site = site
        existing.is_reachable = reachable
        # Honour a human-curated address (ip_locked): only move the unique
        # ip_address when it's free, and never clobber a locked management_ip.
        if not existing.ip_locked:
            existing.management_ip = ip
            if existing.ip_address != ip and not Device.objects.filter(ip_address=ip).exclude(pk=existing.pk).exists():
                existing.ip_address = ip
        existing.last_seen = timezone.now()
        if name and name != existing.hostname and not Device.objects.filter(hostname=name).exclude(pk=existing.pk).exists():
            existing.hostname = name
        existing.save()
        return "updated"

    Device.objects.create(
        hostname=_unique_hostname(name or ip),
        ip_address=ip, management_ip=ip, mac_address=mac,
        vendor="Juniper", model=model, os_version=version,
        platform=platform, role=role, site=site,
        status=Device.Status.ACTIVE, is_reachable=reachable,
        last_seen=timezone.now(),
    )
    return "imported"


def _merge_devices(devices: list, stats: list) -> list:
    """Merge a site's inventory list with its stats list (joined on MAC).

    The ``/devices`` inventory has name/mac/model/type; live ip/version/status
    come from ``/stats/devices``. Returns one dict per device with both merged.
    """
    stats_by_mac = {}
    for s in stats or []:
        mac = _normalize_mac(s.get("mac"))
        if mac:
            stats_by_mac[mac] = s

    merged = []
    seen = set()
    for d in devices or []:
        mac = _normalize_mac(d.get("mac"))
        s = stats_by_mac.get(mac, {})
        seen.add(mac)
        merged.append({
            "name": d.get("name") or s.get("name"),
            "mac": d.get("mac") or s.get("mac"),
            "model": d.get("model") or s.get("model"),
            "type": d.get("type") or s.get("type"),
            "ip": s.get("ip") or d.get("ip"),
            "version": s.get("version") or d.get("version"),
            "status": s.get("status"),
        })
    # Stats may report a device not present in the (cached) inventory list.
    for mac, s in stats_by_mac.items():
        if mac in seen:
            continue
        merged.append({
            "name": s.get("name"), "mac": s.get("mac"), "model": s.get("model"),
            "type": s.get("type"), "ip": s.get("ip"), "version": s.get("version"),
            "status": s.get("status"),
        })
    return merged


def sync_mist() -> dict:
    """
    Pull the configured org's sites + devices and import them. Returns
    ``{"sites", "imported", "updated", "skipped"}``. Stamps last_sync/last_error/
    site_count/device_count on the singleton. Raises MistError on connection
    failure (after recording last_error).
    """
    from django.utils import timezone

    from .mist_client import MistClient, MistError, _read_api_token
    from .models import MistIntegration, MistSite

    integration = MistIntegration.load()
    token = _read_api_token()
    if not token:
        integration.last_error = "No API token configured"
        integration.save(update_fields=["last_error", "updated_at"])
        raise MistError("No Juniper Mist API token configured")

    counts = {"sites": 0, "imported": 0, "updated": 0, "skipped": 0}
    try:
        client = MistClient(token)
        org_id = integration.org_id or ""
        org_name = integration.org_name or ""
        if not org_id:
            org_id, org_name = client.resolve_org()
        sites = client.get_sites(org_id)
    except MistError as exc:
        integration.last_error = str(exc)[:512]
        integration.save(update_fields=["last_error", "updated_at"])
        raise

    for raw_site in sites:
        mist_id = str(raw_site.get("id", ""))
        if not mist_id:
            continue
        counts["sites"] += 1
        mist_site, _ = MistSite.objects.update_or_create(
            mist_id=mist_id,
            defaults={
                "name": raw_site.get("name", "") or mist_id,
                "address": (raw_site.get("address") or "")[:255],
                "country_code": (raw_site.get("country_code") or "")[:4],
            },
        )
        site_imported = 0
        try:
            merged = _merge_devices(client.get_devices(mist_id), client.get_device_stats(mist_id))
            for dev in merged:
                result = _import_device(dev, mist_site)
                counts[result] += 1
                if result in ("imported", "updated"):
                    site_imported += 1
        except MistError as exc:
            # One unreachable site shouldn't abort the whole sync.
            logger.warning("Mist site %s device sync failed: %s", mist_id, exc)
        mist_site.device_count = site_imported
        mist_site.last_sync = timezone.now()
        mist_site.save(update_fields=["device_count", "last_sync", "updated_at"])

    integration.org_id = org_id
    integration.org_name = org_name or integration.org_name
    integration.last_sync = timezone.now()
    integration.last_error = ""
    integration.site_count = counts["sites"]
    integration.device_count = counts["imported"] + counts["updated"]
    integration.save(update_fields=[
        "org_id", "org_name", "last_sync", "last_error", "site_count",
        "device_count", "updated_at",
    ])
    logger.info("Mist sync: %s", counts)
    return counts
