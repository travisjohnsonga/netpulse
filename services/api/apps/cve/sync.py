"""
CVE sync orchestration.

Drives the end-to-end flow used by the cve-engine and the manual-sync endpoint:

  1. Determine which platforms are actually in the device inventory.
  2. Fetch CVEs from NVD (keyword per platform) and upsert them.
  3. Flag CVEs that appear on the CISA KEV list (highest priority).
  4. Optionally pull Cisco PSIRT advisories (only if credentials are configured).
  5. Correlate every CVE to active devices using CPE version matching, falling
     back to keyword/unverified links when the version can't be confirmed.

All network I/O lives in nvd.py / psirt.py / cisa.py; this module is the DB-side
coordinator and records a sync summary on CVEFeedSettings.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from apps.devices.models import Device

from . import nvd
from .models import CVE, CVEFeedSettings, DeviceCVE

logger = logging.getLogger(__name__)


def platforms_to_sync() -> list[str]:
    """Active-inventory platforms that have an NVD keyword mapping."""
    present = (
        Device.objects.filter(status=Device.Status.ACTIVE)
        .exclude(platform="")
        .values_list("platform", flat=True)
        .distinct()
    )
    return [p for p in present if p in nvd.PLATFORM_CPE_PREFIXES]


def upsert_cve(parsed: dict) -> CVE:
    """
    Create/update a CVE row. ``affected_platforms`` is unioned with any existing
    value so a CVE surfaced under several platform keywords accumulates them;
    the rest of the fields are authoritative from the latest parse.
    """
    cve_id = parsed["cve_id"]
    incoming_platforms = set(parsed.get("affected_platforms") or [])
    defaults = {k: v for k, v in parsed.items() if k != "cve_id"}

    existing = CVE.objects.filter(cve_id=cve_id).first()
    if existing:
        merged = sorted(set(existing.affected_platforms or []) | incoming_platforms)
        defaults["affected_platforms"] = merged
        # never downgrade a KEV flag set by the CISA feed
        if existing.cisa_kev:
            defaults["cisa_kev"] = True
    cve, _ = CVE.objects.update_or_create(cve_id=cve_id, defaults=defaults)
    return cve


# ── Correlation ───────────────────────────────────────────────────────────────

def evaluate(device: Device, cve: CVE) -> tuple[str, str] | None:
    """
    Decide how (if at all) ``cve`` applies to ``device``.

    Returns (match_type, detail) for a link to create, or None when the CVE is
    NOT_APPLICABLE (device version known and outside every affected range).
    """
    platform = device.platform
    configs = [c for c in (cve.cpe_configs or []) if c.get("platform") == platform]

    if not configs:
        # Platform surfaced only via keyword association — can't version-check.
        return (DeviceCVE.MatchType.KEYWORD, "platform name match (version unverified)")

    dv = (device.os_version or "").strip()
    if not dv:
        return (DeviceCVE.MatchType.UNVERIFIED, "device version unknown")

    has_constraints = False
    for c in configs:
        if c.get("exact_version") or nvd.config_constraints_apply(c):
            has_constraints = True
        if nvd.version_matches(dv, c):
            mt = (
                DeviceCVE.MatchType.EXACT_VERSION
                if c.get("exact_version")
                else DeviceCVE.MatchType.VERSION_RANGE
            )
            return (mt, _describe(c))

    if has_constraints:
        # Version is known and falls outside all affected ranges → not applicable.
        return None
    # Product-wide CPE (version "*") with no constraints → all versions affected.
    return (DeviceCVE.MatchType.KEYWORD, "product affected (all versions)")


def _describe(config: dict) -> str:
    if config.get("exact_version"):
        return f"affects {config['product']} {config['exact_version']}"
    bits = []
    if config.get("version_start_including"):
        bits.append(f">= {config['version_start_including']}")
    if config.get("version_start_excluding"):
        bits.append(f"> {config['version_start_excluding']}")
    if config.get("version_end_including"):
        bits.append(f"<= {config['version_end_including']}")
    if config.get("version_end_excluding"):
        bits.append(f"< {config['version_end_excluding']}")
    return f"affects {config['product']} " + " and ".join(bits) if bits else f"affects {config['product']}"


def correlate(cves: list[CVE] | None = None) -> dict:
    """
    Correlate the given CVEs (default: all) to active devices. Creates/updates
    DeviceCVE links; never touches is_patched (operator-owned). Returns counts.
    """
    summary = {"links_created": 0, "links_updated": 0, "not_applicable": 0}
    devices = list(Device.objects.filter(status=Device.Status.ACTIVE))
    if not devices:
        return summary
    by_platform: dict[str, list[Device]] = {}
    for d in devices:
        by_platform.setdefault(d.platform, []).append(d)

    qs = cves if cves is not None else CVE.objects.exclude(affected_platforms=[])
    for cve in qs:
        for platform in cve.affected_platforms or []:
            for device in by_platform.get(platform, []):
                verdict = evaluate(device, cve)
                if verdict is None:
                    summary["not_applicable"] += 1
                    continue
                match_type, detail = verdict
                _, created = DeviceCVE.objects.update_or_create(
                    device=device, cve=cve,
                    defaults={"match_type": match_type, "match_detail": detail},
                )
                summary["links_created" if created else "links_updated"] += 1
    return summary


# ── Top-level sync ────────────────────────────────────────────────────────────

def run_sync(*, page_sleep: float | None = None) -> dict:
    """
    Full CVE sync. Returns a summary dict and records it on CVEFeedSettings.
    Safe to call from a management command, a thread, or a test (inject
    page_sleep=0 and monkeypatch the feed clients to avoid network).
    """
    settings_obj = CVEFeedSettings.load()
    settings_obj.last_sync_status = "running"
    settings_obj.save(update_fields=["last_sync_status"])

    summary = {
        "platforms": [], "cves_fetched": 0, "cves_upserted": 0,
        "kev_flagged": 0, "psirt_advisories": 0,
        "links_created": 0, "links_updated": 0, "not_applicable": 0,
    }
    try:
        platforms = platforms_to_sync()
        summary["platforms"] = platforms

        touched: dict[str, CVE] = {}
        if settings_obj.nvd_enabled:
            for platform in platforms:
                logger.info("Fetching CVEs for %s (%s)...",
                            platform, nvd.PLATFORM_CPE_PREFIXES.get(platform))
                for raw in nvd.fetch_platform(platform, page_sleep=page_sleep):
                    parsed = nvd.parse_cve(raw)
                    if not parsed["cve_id"]:
                        continue
                    # The virtualMatchString query guarantees this platform's CPE
                    # is present; record it explicitly in case the CPE parse missed it.
                    if platform not in parsed["affected_platforms"]:
                        parsed["affected_platforms"] = sorted(
                            set(parsed["affected_platforms"]) | {platform},
                        )
                    summary["cves_fetched"] += 1
                    cve = upsert_cve(parsed)
                    summary["cves_upserted"] += 1
                    touched[cve.cve_id] = cve

        # Cisco PSIRT (optional — only when credentials configured).
        try:
            from . import psirt
            for parsed in psirt.fetch_advisories(platforms):
                if not parsed.get("cve_id"):
                    continue
                cve = upsert_cve(parsed)
                touched[cve.cve_id] = cve
                summary["psirt_advisories"] += 1
        except Exception as exc:  # pragma: no cover - PSIRT is best-effort
            logger.warning("Cisco PSIRT sync skipped: %s", exc)

        # CISA KEV flagging.
        if settings_obj.cisa_kev_enabled:
            try:
                from . import cisa
                summary["kev_flagged"] = cisa.flag_known_exploited()
            except Exception as exc:  # pragma: no cover - KEV is best-effort
                logger.warning("CISA KEV flagging skipped: %s", exc)

        # Correlate. When this was an incremental fetch, correlate just the
        # touched CVEs; on an empty fetch (e.g. NVD disabled) correlate all.
        corr = correlate(list(touched.values()) if touched else None)
        summary.update(corr)

        settings_obj.last_sync_status = "ok"
    except Exception as exc:
        logger.exception("CVE sync failed")
        settings_obj.last_sync_status = "error"
        summary["error"] = str(exc)
    finally:
        settings_obj.last_synced_at = timezone.now()
        settings_obj.last_sync_summary = summary
        settings_obj.save(update_fields=[
            "last_synced_at", "last_sync_status", "last_sync_summary",
        ])
    logger.info("CVE sync complete: %s", summary)
    return summary
