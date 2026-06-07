"""
Import UniFi-controller-managed devices into the NetPulse inventory.

Each controller's managed devices (APs/switches/gateways) are mapped to Device
records, keyed by IP so re-syncs update in place. Best-effort: an unreachable
controller records last_error and is skipped, never raising from sync_all.
"""
from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)

# UniFi device 'type' → (NetPulse platform, DeviceRole slug).
UNIFI_TYPE_MAP = {
    "uap": ("unifi_ap", "wireless-ap"),
    "usw": ("unifi_sw", "access-switch"),
    "ugw": ("unifi_gw", "router"),
    "udm": ("unifi_udm", "router"),
}


def _read_password(controller) -> str:
    from apps.credentials import vault
    try:
        return (vault.read_secret(controller.vault_path) or {}).get("password", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("UniFi %s: could not read password from OpenBao: %s", controller.name, exc)
        return ""


def _valid_ip(value) -> str:
    try:
        ipaddress.ip_address(str(value))
        return str(value)
    except (ValueError, TypeError):
        return ""


def _unique_hostname(base: str) -> str:
    """Return a hostname not already taken (Device.hostname is unique)."""
    from apps.devices.models import Device
    name = base
    n = 1
    while Device.objects.filter(hostname=name).exists():
        n += 1
        name = f"{base}-{n}"
    return name


def _import_device(raw: dict, controller) -> str:
    """Create/update a Device from one UniFi device dict. Returns
    'imported' | 'updated' | 'skipped'."""
    from django.db.models import Q
    from django.utils import timezone

    from apps.devices.models import Device, DeviceRole

    ip = _valid_ip(raw.get("ip"))
    if not ip:
        return "skipped"  # no usable IP → can't create (ip_address is required+unique)

    platform, role_slug = UNIFI_TYPE_MAP.get((raw.get("type") or "").lower(), ("other", None))
    role = DeviceRole.objects.filter(slug=role_slug).first() if role_slug else None
    name = (raw.get("name") or raw.get("mac") or ip).strip()
    model = (raw.get("model") or "").strip()
    version = (raw.get("version") or "").strip()

    existing = Device.objects.filter(Q(management_ip=ip) | Q(ip_address=ip)).first()
    if existing:
        existing.platform = platform
        existing.vendor = existing.vendor or "Ubiquiti"
        if model:
            existing.model = model
        if version:
            existing.os_version = version
        if role and not existing.role_id:
            existing.role = role
        if controller.site_id and not existing.site_id:
            existing.site = controller.site
        existing.last_seen = timezone.now()
        # Only adopt the controller hostname if it's free (don't break uniqueness).
        if name and name != existing.hostname and not Device.objects.filter(hostname=name).exclude(pk=existing.pk).exists():
            existing.hostname = name
        existing.save()
        return "updated"

    Device.objects.create(
        hostname=_unique_hostname(name or ip),
        ip_address=ip, management_ip=ip,
        vendor="Ubiquiti", model=model, os_version=version,
        platform=platform, role=role, site=controller.site,
        status=Device.Status.ACTIVE, last_seen=timezone.now(),
    )
    return "imported"


def sync_controller(controller) -> dict:
    """
    Pull a controller's devices and import them. Returns
    ``{"imported", "updated", "skipped"}``. Records last_sync/last_error/
    device_count on the controller. Raises UnifiError on connection failure.
    """
    from django.utils import timezone

    from .unifi_client import UnifiClient, UnifiError

    counts = {"imported": 0, "updated": 0, "skipped": 0}
    try:
        with UnifiClient(controller.host, controller.port, controller.username,
                         _read_password(controller), site_id=controller.unifi_site_id,
                         verify_ssl=controller.verify_ssl) as client:
            devices = client.get_devices()
        for raw in devices:
            counts[_import_device(raw, controller)] += 1
    except UnifiError as exc:
        controller.last_error = str(exc)[:512]
        controller.save(update_fields=["last_error", "updated_at"])
        raise

    controller.last_sync = timezone.now()
    controller.last_error = ""
    controller.device_count = counts["imported"] + counts["updated"]
    controller.save(update_fields=["last_sync", "last_error", "device_count", "updated_at"])
    logger.info("UniFi %s sync: %s", controller.name, counts)
    return counts


def sync_all_controllers() -> dict:
    """Sync every enabled controller (best-effort per controller)."""
    from .models import UnifiController

    totals = {"controllers": 0, "imported": 0, "updated": 0, "skipped": 0, "failed": 0}
    for controller in UnifiController.objects.filter(enabled=True):
        totals["controllers"] += 1
        try:
            c = sync_controller(controller)
            for k in ("imported", "updated", "skipped"):
                totals[k] += c[k]
        except Exception as exc:  # noqa: BLE001
            totals["failed"] += 1
            logger.warning("UniFi controller %s sync failed: %s", controller.name, exc)
    return totals
