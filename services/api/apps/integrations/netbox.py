"""
Minimal NetBox API client + import logic.

Supports NetBox v3.x and v4.x. The main API difference we care about is the
device role field: v3 exposes ``device_role``, v4 renamed it to ``role``. We
read whichever is present.

Uses stdlib urllib (no extra deps). ``NetBoxClient`` is injectable so the import
logic can be unit-tested with a fake client (no network).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from django.utils import timezone

# NetPulse platform slugs we can map onto, by NetBox platform/slug substring.
_PLATFORM_MAP = [
    ("ios-xe", "ios_xe"), ("iosxe", "ios_xe"),
    ("ios-xr", "ios_xr"), ("iosxr", "ios_xr"),
    ("nx-os", "nxos"), ("nxos", "nxos"),
    ("ios", "ios"),
    ("eos", "eos"), ("arista", "eos"),
    ("junos", "junos"), ("juniper", "junos"),
    ("sonic", "sonic"),
]

# NetBox device status value → NetPulse Device.Status value.
_STATUS_MAP = {
    "active": "active",
    "planned": "inactive",     # NetPulse has no "pending" device status
    "staged": "inactive",
    "offline": "inactive",
    "failed": "inactive",
    "decommissioning": "decommissioned",
    "inventory": "inactive",
}


class NetBoxError(Exception):
    pass


class NetBoxClient:
    def __init__(self, url: str, token: str, timeout: float = 15.0):
        self.base = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _get(self, path: str) -> dict:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise NetBoxError(f"NetBox returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise NetBoxError(f"Could not reach NetBox: {exc.reason}") from exc

    def detect_version(self) -> str:
        data = self._get("/api/status/")
        return str(data.get("netbox-version", ""))

    def _paginate(self, path: str) -> list[dict]:
        results: list[dict] = []
        next_path: str | None = f"{path}?limit=200"
        while next_path:
            data = self._get(next_path)
            results.extend(data.get("results", []))
            nxt = data.get("next")
            if nxt:
                parsed = urllib.parse.urlparse(nxt)
                next_path = parsed.path + ("?" + parsed.query if parsed.query else "")
            else:
                next_path = None
        return results

    def get_sites(self) -> list[dict]:
        return self._paginate("/api/dcim/sites/")

    def get_devices(self) -> list[dict]:
        return self._paginate("/api/dcim/devices/")


def map_platform(name: str) -> str:
    low = (name or "").lower()
    for needle, slug in _PLATFORM_MAP:
        if needle in low:
            return slug
    return "other"


def _device_role(nb_device: dict) -> str:
    role = nb_device.get("role") or nb_device.get("device_role") or {}
    return (role or {}).get("name", "") if isinstance(role, dict) else ""


def run_import(client: NetBoxClient, options: dict) -> dict:
    """
    Import sites then devices from NetBox. Returns a summary dict. Skips devices
    that already exist (by hostname or IP) or lack a primary IP. Never raises for
    per-record problems — collects them in ``errors``.
    """
    from apps.devices.models import Device, Site

    summary = {"sites_imported": 0, "devices_imported": 0, "devices_updated": 0, "skipped": 0, "errors": []}

    site_by_name: dict[str, Site] = {}

    if options.get("sites", True):
        for nb in client.get_sites():
            name = nb.get("name")
            if not name:
                continue
            site, created = Site.objects.get_or_create(
                name=name,
                defaults={
                    "description": nb.get("description", "") or "",
                    "address": nb.get("physical_address", "") or "",
                    "site_type": "datacenter",
                },
            )
            site_by_name[name] = site
            if created:
                summary["sites_imported"] += 1

    if options.get("devices", True):
        for nb in client.get_devices():
            try:
                hostname = nb.get("name")
                if not hostname:
                    summary["skipped"] += 1
                    continue
                primary = (nb.get("primary_ip") or {})
                ip_cidr = primary.get("address") if isinstance(primary, dict) else None
                ip = ip_cidr.split("/")[0] if ip_cidr else None
                if not ip:
                    summary["errors"].append(f"{hostname}: no primary IP — skipped")
                    summary["skipped"] += 1
                    continue
                # Upsert by hostname (stable identity across re-imports). Skip
                # only when the IP is already owned by a *different* hostname,
                # since ip_address is globally unique.
                ip_owner = Device.objects.filter(ip_address=ip).exclude(hostname=hostname).first()
                if ip_owner:
                    summary["errors"].append(f"{hostname}: IP {ip} already used by {ip_owner.hostname} — skipped")
                    summary["skipped"] += 1
                    continue

                dtype = nb.get("device_type") or {}
                manufacturer = (dtype.get("manufacturer") or {}).get("name", "") if isinstance(dtype, dict) else ""
                model = dtype.get("model", "") if isinstance(dtype, dict) else ""
                platform_obj = nb.get("platform") or {}
                platform_name = (platform_obj or {}).get("name", "") if isinstance(platform_obj, dict) else ""
                status_val = (nb.get("status") or {}).get("value", "active") if isinstance(nb.get("status"), dict) else "active"

                nb_site = nb.get("site") or {}
                site_name = nb_site.get("name") if isinstance(nb_site, dict) else None
                site = site_by_name.get(site_name) if site_name else None
                if site is None and site_name:
                    site = Site.objects.filter(name=site_name).first()

                role = _device_role(nb)
                tags = [t.get("name") for t in (nb.get("tags") or []) if isinstance(t, dict)]
                notes_bits = []
                if role:
                    notes_bits.append(f"Role: {role}")
                if tags:
                    notes_bits.append(f"Tags: {', '.join(tags)}")
                notes_bits.append("Imported from NetBox")

                _, created = Device.objects.update_or_create(
                    hostname=hostname,
                    defaults=dict(
                        ip_address=ip,
                        management_ip=ip,
                        vendor=manufacturer,
                        model=model,
                        platform=map_platform(platform_name or model),
                        status=_STATUS_MAP.get(status_val, "inactive"),
                        site=site,
                        notes="\n".join(notes_bits),
                    ),
                )
                summary["devices_imported" if created else "devices_updated"] += 1
            except Exception as exc:  # never let one record abort the import
                summary["errors"].append(f"{nb.get('name', '?')}: {exc}")
                summary["skipped"] += 1

    summary["finished_at"] = timezone.now()
    return summary
