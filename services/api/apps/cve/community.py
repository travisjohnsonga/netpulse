"""
Community advisory feed loader.

Parses vendor advisory YAML (advisories/<vendor>/*.yaml) for vendors without a
machine-readable API (Juniper JSAs, Arista advisories, …), upserts CVE rows,
and correlates them to active devices on the affected platforms.

See advisories/README.md for the YAML schema.
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_SEVERITY = {"critical", "high", "medium", "low"}


def load_advisory_files(directory: str | Path) -> list[dict]:
    """Parse every *.yaml/*.yml under directory/* into a flat list of advisories."""
    import yaml

    root = Path(directory)
    advisories: list[dict] = []
    if not root.is_dir():
        return advisories
    for path in sorted(root.glob("*/*.y*ml")):
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except Exception as exc:
            logger.warning("skipping unreadable advisory file %s: %s", path, exc)
            continue
        items = doc.get("advisories") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and (item.get("id") or item.get("cve_ids")):
                item.setdefault("_source_file", str(path))
                advisories.append(item)
    return advisories


def _primary_id(adv: dict) -> str:
    cves = adv.get("cve_ids") or []
    return (cves[0] if cves else adv.get("id") or "").strip()


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=_dt.timezone.utc)
    try:
        return _dt.datetime.fromisoformat(str(value)).replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def sync_advisories(directory: str | Path) -> dict:
    """
    Upsert CVEs from the community YAML and correlate to active devices on the
    affected platforms. Returns a summary dict.
    """
    from apps.cve.models import CVE, DeviceCVE
    from apps.devices.models import Device

    summary = {"advisories": 0, "cves_upserted": 0, "device_links": 0, "skipped": 0}
    advisories = load_advisory_files(directory)
    summary["advisories"] = len(advisories)

    for adv in advisories:
        cve_id = _primary_id(adv)
        severity = str(adv.get("severity", "")).lower()
        if not cve_id or severity not in _VALID_SEVERITY:
            summary["skipped"] += 1
            continue

        cve, _ = CVE.objects.update_or_create(
            cve_id=cve_id,
            defaults={
                "description": adv.get("description") or adv.get("title") or cve_id,
                "severity": severity,
                "cvss_score": adv.get("cvss_score"),
                "cvss_vector": adv.get("cvss_vector") or "",
                "published_at": _parse_date(adv.get("published")),
                "source_url": adv.get("url") or "",
            },
        )
        summary["cves_upserted"] += 1

        platforms = ((adv.get("affected") or {}).get("platforms")) or []
        if not platforms:
            continue
        devices = Device.objects.filter(status=Device.Status.ACTIVE, platform__in=platforms)
        for device in devices:
            _, created = DeviceCVE.objects.get_or_create(device=device, cve=cve)
            if created:
                summary["device_links"] += 1

    logger.info("community advisories: %s", summary)
    return summary
