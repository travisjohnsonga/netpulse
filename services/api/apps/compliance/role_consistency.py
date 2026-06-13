"""
Cross-device consistency engine.

Compares one piece of config (VLAN database, NTP/DNS servers, SNMP, AAA, banner)
across every device sharing a role/platform/site and flags drift. The "expected"
set is the majority vote across the group; each device is reported as missing or
having extra items relative to it.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def expand_vlan_range(vlan_str: str) -> set[int]:
    """Expand ``"1,10,20-30"`` → ``{1, 10, 20, 21, ... 30}``."""
    vlans: set[int] = set()
    for part in (vlan_str or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                vlans |= set(range(int(start), int(end) + 1))
            except ValueError:
                pass
        else:
            try:
                vlans.add(int(part))
            except ValueError:
                pass
    return vlans


def parse_vlans_from_config(config: str, platform: str) -> set[int]:
    """Parse the set of configured VLAN IDs from a device config."""
    vlans: set[int] = set()
    if not config:
        return vlans
    plat = (platform or "").lower()
    if plat in ("aos_cx", "aos_s", "aruba"):
        # AOS-CX/AOS-S: top-level "vlan 10" or "vlan 10,20,30-40".
        for m in re.finditer(r"^vlan\s+([\d,\-]+)", config, re.MULTILINE | re.IGNORECASE):
            vlans |= expand_vlan_range(m.group(1))
    else:
        # Cisco-style: "vlan 10" / "switchport access vlan 10" / "switchport trunk
        # allowed vlan 10,20-30". Capture every "vlan <ids>" occurrence.
        for m in re.finditer(r"\bvlan\s+([\d,\-]+)", config, re.IGNORECASE):
            vlans |= expand_vlan_range(m.group(1))
    return vlans


def _latest_config_content(device) -> str:
    from apps.configbackup.models import DeviceConfig
    cfg = DeviceConfig.objects.filter(device=device).order_by("-collected_at").first()
    return cfg.content if cfg and cfg.content else ""


# Per check-type capture patterns; group(1) is the comparable value.
_LINE_PATTERNS: dict[str, list[str]] = {
    "ntp_consistency": [r"ntp\s+server\s+(\S+)"],
    "dns_consistency": [r"(?:ip\s+)?name-?server\s+(\S+)", r"dns\s+server-address\s+(\S+)"],
    "snmp_consistency": [r"snmp-?server\s+community\s+(\S+)", r"snmp-?server\s+host\s+(\S+)"],
    "aaa_consistency": [r"(?:radius|tacacs)(?:-server)?\s+host\s+(\S+)",
                        r"(?:radius|tacacs)-server\s+(\S+)"],
    "banner_consistency": [r"^\s*banner\s+\w+\s+(.+)$"],
}


def get_device_config_lines(device, check_type: str) -> set[str]:
    """Extract the comparable token set for a non-VLAN consistency check."""
    content = _latest_config_content(device)
    items: set[str] = set()
    for pat in _LINE_PATTERNS.get(check_type, []):
        for m in re.finditer(pat, content, re.IGNORECASE | re.MULTILINE):
            items.add(m.group(1).strip().lower())
    return items


def get_device_vlans(device) -> set[int]:
    """VLAN IDs configured on a device.

    Sources, in priority order: AOS-CX REST API (best-effort; skipped/ignored on
    any failure), then the latest config backup.
    """
    if device.platform == "aos_cx" and device.credential_profile_id and device.management_ip:
        try:
            from apps.credentials import vault
            from apps.devices.aos_cx_client import AOSCXClient
            profile = device.credential_profile
            secrets = vault.read_secret(profile.vault_path) or {}
            with AOSCXClient(str(device.management_ip)) as client:
                client.login(profile.ssh_username, secrets.get("ssh_password", ""))
                data = client._get("system/vlans", params={"depth": 1}) or {}
            rest = {int(k) for k in data.keys() if str(k).isdigit()}
            if rest:
                return rest
        except Exception as exc:  # noqa: BLE001
            logger.debug("REST VLAN fetch failed for %s: %s", device.hostname, exc)
    return parse_vlans_from_config(_latest_config_content(device), device.platform)


def _device_items(device, rule):
    """The comparable set for one device under a rule (VLANs as ints, else strings)."""
    if rule.check_type == "vlan_consistency":
        excluded = {int(x) for x in (rule.excluded_vlans or []) if str(x).isdigit()}
        return get_device_vlans(device) - excluded
    return get_device_config_lines(device, rule.check_type)


def _remediation(device, missing, extra, rule) -> str:
    """Suggested config to bring a device back in line (VLAN check only)."""
    if rule.check_type != "vlan_consistency":
        return ""
    lines = []
    for v in sorted(missing):
        lines.append(f"vlan {v}")
    for v in sorted(extra):
        lines.append(f"no vlan {v}")
    return "\n".join(lines)


def run_role_consistency(rule, persist: bool = True) -> dict:
    """Compare the rule's config item across the scoped group; report drift."""
    from django.utils import timezone

    from apps.devices.models import Device

    qs = Device.objects.filter(status=Device.Status.ACTIVE)
    if rule.role_id:
        qs = qs.filter(role_id=rule.role_id)
    if rule.platform:
        qs = qs.filter(platform=rule.platform)
    if rule.site_id:
        qs = qs.filter(site_id=rule.site_id)
    devices = list(qs.select_related("role", "site"))

    if len(devices) < 2:
        summary = {"status": "skip", "reason": "Need at least 2 devices to compare",
                   "total_devices": len(devices), "passing": 0, "failing": 0,
                   "expected": [], "check_type": rule.check_type}
        if persist:
            rule.last_run = timezone.now()
            rule.last_summary = summary
            rule.save(update_fields=["last_run", "last_summary", "updated_at"])
        return {"rule_id": rule.id, "rule": rule.name, **summary, "results": []}

    per_device = {d.id: {"device": d, "items": _device_items(d, rule)} for d in devices}

    # Expected = items present on a strict majority of devices.
    all_items: set = set()
    for d in per_device.values():
        all_items |= d["items"]
    total = len(per_device)
    expected = {it for it in all_items
                if sum(1 for d in per_device.values() if it in d["items"]) > total / 2}

    results = []
    for data in per_device.values():
        dev, items = data["device"], data["items"]
        missing = expected - items
        extra = items - expected
        ok = not missing and not extra
        results.append({
            "device_id": dev.id, "device": dev.hostname, "status": "pass" if ok else "fail",
            "missing": sorted(missing), "extra": sorted(extra),
            "has": sorted(items), "expected": sorted(expected),
            "remediation": "" if ok else _remediation(dev, missing, extra, rule),
        })
    results.sort(key=lambda r: (r["status"] != "fail", r["device"]))

    summary = {
        "status": "complete", "check_type": rule.check_type,
        "expected": sorted(expected), "total_devices": total,
        "passing": sum(1 for r in results if r["status"] == "pass"),
        "failing": sum(1 for r in results if r["status"] == "fail"),
    }
    if persist:
        rule.last_run = timezone.now()
        rule.last_summary = summary
        rule.save(update_fields=["last_run", "last_summary", "updated_at"])
    logger.info("role-consistency %s: %s", rule.name, summary)
    return {"rule_id": rule.id, "rule": rule.name, **summary, "results": results}
