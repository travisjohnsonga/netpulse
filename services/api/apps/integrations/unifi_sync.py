"""
Import UniFi-controller-managed devices into the NetPulse inventory.

Each controller's managed devices (APs/switches/gateways) are mapped to Device
records, keyed by IP so re-syncs update in place. Best-effort: an unreachable
controller records last_error and is skipped, never raising from sync_all.
"""
from __future__ import annotations

import ipaddress
import logging
import re

logger = logging.getLogger(__name__)

# UniFi device 'type' → (NetPulse platform, DeviceRole slug).
UNIFI_TYPE_MAP = {
    "uap": ("unifi_ap", "wireless-ap"),
    "usw": ("unifi_sw", "access-switch"),
    "ugw": ("unifi_gw", "router"),
    "udm": ("unifi_udm", "router"),
}


def get_controller_credentials(controller, profile=None) -> tuple[str, str]:
    """Return ``(username, password)`` for a controller's local API access.

    Credentials come from a CredentialProfile (the same system the rest of
    NetPulse uses), not from fields on the controller: the username is on the
    profile (``https_username`` / ``ssh_username``) and the password lives in
    OpenBao at the profile's ``vault_path``. HTTPS credentials are preferred,
    falling back to SSH. ``profile`` overrides the controller's saved profile
    (used by the test endpoint to try a not-yet-saved selection). Raises
    :class:`UnifiError` with an actionable message when nothing usable is found.
    """
    from apps.credentials import vault

    from .unifi_client import UnifiError

    profile = profile or controller.credential_profile
    if profile is None:
        raise UnifiError(
            f"No credential profile assigned to controller {controller.name}. "
            "Assign a profile with HTTPS/API credentials in Settings → "
            "Integrations → UniFi."
        )
    secrets = vault.read_secret(profile.vault_path) or {}
    if profile.https_enabled:
        username = (profile.https_username or "").strip()
        password = secrets.get("https_password", "") or ""
    elif profile.ssh_enabled:
        username = (profile.ssh_username or "").strip()
        password = secrets.get("ssh_password", "") or ""
    else:
        raise UnifiError(
            f'Credential profile "{profile.name}" has no HTTPS or SSH '
            "credentials. Enable HTTPS credentials for UniFi controller access."
        )
    if not username or not password:
        raise UnifiError(
            f'No credentials found in profile "{profile.name}". '
            "Check the HTTPS username and password are set."
        )
    return username, password


def _valid_ip(value) -> str:
    try:
        ipaddress.ip_address(str(value))
        return str(value)
    except (ValueError, TypeError):
        return ""


def _normalize_mac(value) -> str:
    """Canonicalise a MAC to lowercase colon form, or '' if not a valid MAC.

    Accepts colon/dash/dot/bare forms (e.g. ``AA-BB-...``, ``aabb.ccdd.eeff``)
    and normalises to ``aa:bb:cc:dd:ee:ff`` so lookups match regardless of how
    the controller formats it.
    """
    hexes = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    if len(hexes) != 12:
        return ""
    hexes = hexes.lower()
    return ":".join(hexes[i:i + 2] for i in range(0, 12, 2))


def find_existing_unifi_device(mac: str, ip: str, hostname: str, platform: str):
    """Find the Device this UniFi device already maps to, by stable identity.

    Priority: MAC (stable across IP changes) → IP (``management_ip``/``ip_address``)
    → hostname+platform. Returns the Device or None. Keying on MAC first is what
    stops a changed IP from spawning a duplicate ``<name>-2`` record.
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
    from django.utils import timezone

    from apps.devices.models import Device, DeviceRole

    ip = _valid_ip(raw.get("ip"))
    if not ip:
        return "skipped"  # no usable IP → can't create (ip_address is required+unique)

    mac = _normalize_mac(raw.get("mac"))
    platform, role_slug = UNIFI_TYPE_MAP.get((raw.get("type") or "").lower(), ("other", None))
    role = DeviceRole.objects.filter(slug=role_slug).first() if role_slug else None
    name = (raw.get("name") or raw.get("mac") or ip).strip()
    model = (raw.get("model") or "").strip()
    version = (raw.get("version") or "").strip()

    # Match by stable identity (MAC first) so a device that changed IP updates in
    # place instead of spawning a duplicate.
    existing = find_existing_unifi_device(mac, ip, name, platform)
    if existing:
        existing.platform = platform
        existing.vendor = existing.vendor or "Ubiquiti"
        if mac and existing.mac_address != mac:
            existing.mac_address = mac
        if model:
            existing.model = model
        if version:
            existing.os_version = version
        if role and not existing.role_id:
            existing.role = role
        if controller.site_id and not existing.site_id:
            existing.site = controller.site
        # Track the device's current address (it may have changed). management_ip
        # is non-unique; only move the unique ip_address when it's free. Honour
        # ip_locked: a human-curated management_ip must not be clobbered by sync
        # (the UniFi cloud often reports a WAN IP for consoles).
        if not existing.ip_locked:
            existing.management_ip = ip
            if existing.ip_address != ip and not Device.objects.filter(ip_address=ip).exclude(pk=existing.pk).exists():
                existing.ip_address = ip
        existing.last_seen = timezone.now()
        # Only adopt the controller hostname if it's free (don't break uniqueness).
        if name and name != existing.hostname and not Device.objects.filter(hostname=name).exclude(pk=existing.pk).exists():
            existing.hostname = name
        existing.save()
        return "updated"

    Device.objects.create(
        hostname=_unique_hostname(name or ip),
        ip_address=ip, management_ip=ip, mac_address=mac,
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
        username, password = get_controller_credentials(controller)
        with UnifiClient(controller.host, controller.port, username,
                         password, site_id=controller.unifi_site_id,
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


def update_linked_device_host(controller) -> bool:
    """Propagate a controller's current ``host`` (mgmt IP) to the Device that
    represents it, so editing or re-discovering a controller whose IP changed
    doesn't leave its device pointing at a stale address before the next sync.

    The device is found via the console-status link (reliable FK back to the
    controller); if none exists yet there's nothing to update. Best-effort and
    a no-op when ``host`` is a DNS name rather than an IP. Returns True if a
    device was updated.
    """
    from apps.devices.models import Device

    ip = _valid_ip(controller.host)
    if not ip:
        return False

    status = controller.console_statuses.first()
    device = status.device if (status and status.device_id) else None
    if device is None:
        return False
    # Don't clobber a human-curated address (the cloud host record often reports
    # the console's WAN IP, not its LAN management IP).
    if device.ip_locked:
        return False

    fields: list[str] = []
    if device.management_ip != ip:
        device.management_ip = ip
        fields.append("management_ip")
    if device.ip_address != ip and not Device.objects.filter(ip_address=ip).exclude(pk=device.pk).exists():
        device.ip_address = ip
        fields.append("ip_address")
    if not fields:
        return False
    device.save(update_fields=[*fields, "updated_at"])
    logger.info("UniFi %s: propagated host %s to device %s", controller.name, ip, device.hostname)
    return True


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
