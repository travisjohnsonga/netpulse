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
import ssl
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
    def __init__(self, url: str, token: str, timeout: float = 15.0, verify_ssl: bool = True):
        self.base = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        # When verification is disabled (internal NetBox with a self-signed
        # cert) pass an unverified TLS context to urlopen; HTTP URLs ignore it.
        # verify_ssl=True keeps the default (context=None → full verification).
        self._ssl_ctx: ssl.SSLContext | None = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_ctx = ctx

    def _get(self, path: str) -> dict:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise NetBoxError(f"NetBox returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise NetBoxError(f"Could not reach NetBox: {exc.reason}") from exc

    def detect_version(self) -> str:
        """Return the NetBox version, or "unknown" if the connection works but
        the version can't be read.

        ``_get`` always sends the API token, but some NetBox deployments require
        auth on every endpoint and still reject ``/api/status/`` (custom
        permissions). On a 401/403 there, fall back to a tiny authenticated read
        to confirm the connection is actually good and report the version as
        unknown rather than failing the whole import.
        """
        try:
            data = self._get("/api/status/")
            return str(data.get("netbox-version", ""))
        except NetBoxError as exc:
            if "401" in str(exc) or "403" in str(exc):
                self._get("/api/dcim/sites/?limit=1")  # re-raises if truly unauth/unreachable
                return "unknown"
            raise

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


def _resolve_role_obj(role_name: str):
    """Match a NetBox role name to an existing DeviceRole (None if unmatched)."""
    if not role_name:
        return None
    from apps.devices.models import DeviceRole
    return DeviceRole.objects.filter(name__iexact=role_name).first()


def _compute_device(nb: dict, site_by_name: dict):
    """
    Extract NetPulse device fields from a NetBox device. Returns ``(info, reason)``:
    ``info`` is None only when there's no hostname; ``reason`` is a skip message
    (e.g. no IP). For preview, sites are resolved against existing rows only.
    """
    hostname = nb.get("name")
    if not hostname:
        return None, "No hostname in NetBox"
    primary = nb.get("primary_ip") or {}
    ip_cidr = primary.get("address") if isinstance(primary, dict) else None
    ip = ip_cidr.split("/")[0] if ip_cidr else None

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
        from apps.devices.models import Site
        site = Site.objects.filter(name=site_name).first()

    info = {
        "hostname": hostname, "ip": ip, "vendor": manufacturer, "model": model,
        "platform": map_platform(platform_name or model),
        "status": _STATUS_MAP.get(status_val, "inactive"),
        "site": site, "site_name": site_name, "role_name": _device_role(nb),
    }
    return info, (None if ip else "No IP address in NetBox")


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
                role_obj = _resolve_role_obj(role)
                tags = [t.get("name") for t in (nb.get("tags") or []) if isinstance(t, dict)]
                notes_bits = []
                if role:
                    notes_bits.append(f"Role: {role}")
                if tags:
                    notes_bits.append(f"Tags: {', '.join(tags)}")
                notes_bits.append("Imported from NetBox")

                defaults = dict(
                    ip_address=ip,
                    management_ip=ip,
                    vendor=manufacturer,
                    model=model,
                    platform=map_platform(platform_name or model),
                    status=_STATUS_MAP.get(status_val, "inactive"),
                    site=site,
                    notes="\n".join(notes_bits),
                )
                # Only set role when it maps to an existing DeviceRole, so a
                # re-import never nulls a manually-assigned role.
                if role_obj:
                    defaults["role"] = role_obj
                device, created = Device.objects.update_or_create(
                    hostname=hostname, defaults=defaults,
                )
                # Inherit a site credential profile if none is set.
                from apps.credentials.site_resolve import apply_site_credential
                apply_site_credential(device)
                summary["devices_imported" if created else "devices_updated"] += 1
            except Exception as exc:  # never let one record abort the import
                summary["errors"].append(f"{nb.get('name', '?')}: {exc}")
                summary["skipped"] += 1

    summary["finished_at"] = timezone.now()
    return summary


def preview_import(client: NetBoxClient, options: dict) -> dict:
    """
    Dry-run: compute what an import would create/update/skip — and which credential
    each device would inherit — WITHOUT writing anything. Returns the preview dict
    (``summary`` + per-device ``devices`` + ``credentials`` assignment counts).
    """
    from apps.credentials.site_resolve import resolve_credential
    from apps.devices.models import Device

    site_by_name: dict = {}   # preview never creates sites; resolve existing only
    devices: list[dict] = []
    will = {"create": 0, "update": 0, "skip": 0}
    cred_counts: dict[str, int] = {}
    no_cred = 0

    for nb in client.get_devices():
        info, reason = _compute_device(nb, site_by_name)
        if info is None:
            devices.append({"action": "skip", "hostname": nb.get("name") or "?",
                            "ip": None, "platform": "unknown", "reason": reason})
            will["skip"] += 1
            continue
        if reason:  # no IP
            devices.append({"action": "skip", "hostname": info["hostname"], "ip": info["ip"],
                            "platform": info["platform"], "reason": reason})
            will["skip"] += 1
            continue

        ip, hostname = info["ip"], info["hostname"]
        ip_owner = Device.objects.filter(ip_address=ip).exclude(hostname=hostname).first()
        if ip_owner:
            devices.append({"action": "skip", "hostname": hostname, "ip": ip,
                            "platform": info["platform"],
                            "reason": f"IP {ip} already used by {ip_owner.hostname}"})
            will["skip"] += 1
            continue

        role_obj = _resolve_role_obj(info["role_name"])
        cred = resolve_credential(info["site"].id if info["site"] else None,
                                  role_obj.id if role_obj else None)
        cred_name = cred.name if cred else None

        existing = Device.objects.filter(hostname=hostname).first()
        entry = {"hostname": hostname, "ip": ip, "platform": info["platform"],
                 "site": info["site_name"], "role": info["role_name"] or None,
                 "credential": cred_name, "reason": None}
        if existing:
            changes = []
            if existing.platform != info["platform"]:
                changes.append("platform")
            if existing.site_id != (info["site"].id if info["site"] else None):
                changes.append("site")
            if role_obj and existing.role_id != role_obj.id:
                changes.append("role")
            if existing.status != info["status"]:
                changes.append("status")
            if (existing.model or "") != (info["model"] or ""):
                changes.append("model")
            entry.update(action="update", existing_id=existing.id, changes=changes)
            will["update"] += 1
        else:
            entry["action"] = "create"
            will["create"] += 1
        devices.append(entry)

        if cred_name:
            cred_counts[cred_name] = cred_counts.get(cred_name, 0) + 1
        else:
            no_cred += 1

    return {
        "summary": {"total": len(devices), "will_create": will["create"],
                    "will_update": will["update"], "will_skip": will["skip"]},
        "devices": devices,
        "credentials": {"assignments": cred_counts, "no_match": no_cred},
    }
