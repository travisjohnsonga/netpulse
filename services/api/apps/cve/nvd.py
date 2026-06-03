"""
NVD (National Vulnerability Database) API 2.0 client + parsing helpers.

Fetches CVEs by keyword, normalises the NVD JSON into the fields NetPulse stores
on the ``CVE`` model, extracts CPE version constraints for version matching, and
maps NVD products to NetPulse platform keys.

Network I/O is isolated in ``iter_cves``/``fetch_keyword`` so the parsing and
version-matching helpers are pure and unit-testable without hitting NVD.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Iterator

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# NetPulse platform key → NVD keywordSearch terms. Only platforms present in the
# device inventory are actually fetched (see sync.platforms_to_sync).
PLATFORM_KEYWORDS: dict[str, list[str]] = {
    "ios": ["Cisco IOS"],
    "ios_xe": ["Cisco IOS XE"],
    "ios_xr": ["Cisco IOS XR"],
    "nxos": ["Cisco NX-OS"],
    "fortios": ["Fortinet FortiOS"],
    "panos": ["Palo Alto PAN-OS"],
    "junos": ["Juniper Junos"],
    "eos": ["Arista EOS"],
    "aos_cx": ["Aruba AOS-CX"],
    "aruba": ["Aruba ArubaOS"],
    "sonicwall": ["SonicWall SonicOS"],
}

# CPE 2.3 product token → NetPulse platform key. Used to decide which CPE match
# criteria in a CVE's configurations are relevant, and for version matching.
CPE_PRODUCT_PLATFORM: dict[str, str] = {
    "ios": "ios",
    "ios_xe": "ios_xe",
    "ios_xr": "ios_xr",
    "nx-os": "nxos",
    "nx_os": "nxos",
    "nxos": "nxos",
    "fortios": "fortios",
    "pan-os": "panos",
    "pan_os": "panos",
    "junos": "junos",
    "eos": "eos",
    "arubaos-cx": "aos_cx",
    "arubaos_cx": "aos_cx",
    "aoscx": "aos_cx",
    "arubaos": "aruba",
    "sonicos": "sonicwall",
}

_SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "NONE": "none",
}


# ── Parsing ───────────────────────────────────────────────────────────────────

def _english_description(cve: dict) -> str:
    for d in cve.get("descriptions", []):
        if d.get("lang") == "en":
            return d.get("value", "")
    descs = cve.get("descriptions") or []
    return descs[0].get("value", "") if descs else ""


def _best_cvss(cve: dict) -> tuple[float | None, str, str]:
    """Return (base_score, vector, severity) from the best available CVSS metric.

    Prefers CVSS v3.1 > v3.0 > v2; within a version, the Primary source.
    """
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV31", "cvssMetricV30"):
        entries = metrics.get(key) or []
        chosen = _primary_or_first(entries)
        if chosen:
            data = chosen.get("cvssData", {})
            sev = (data.get("baseSeverity") or "").upper()
            return (
                _to_float(data.get("baseScore")),
                data.get("vectorString", "") or "",
                _SEVERITY_MAP.get(sev, "none"),
            )
    entries = metrics.get("cvssMetricV2") or []
    chosen = _primary_or_first(entries)
    if chosen:
        data = chosen.get("cvssData", {})
        # v2 carries severity at the entry level, not in cvssData.
        sev = (chosen.get("baseSeverity") or "").upper()
        return (
            _to_float(data.get("baseScore")),
            data.get("vectorString", "") or "",
            _SEVERITY_MAP.get(sev, "none"),
        )
    return (None, "", "none")


def _primary_or_first(entries: list[dict]) -> dict | None:
    if not entries:
        return None
    for e in entries:
        if e.get("type") == "Primary":
            return e
    return entries[0]


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_cpe_configs(cve: dict) -> list[dict]:
    """
    Flatten the CVE's ``configurations`` into the CPE match criteria NetPulse
    cares about, keyed to NetPulse platforms. Only ``vulnerable`` cpeMatch
    entries whose product maps to a known platform are kept.
    """
    out: list[dict] = []
    for config in cve.get("configurations", []) or []:
        for node in config.get("nodes", []) or []:
            for match in node.get("cpeMatch", []) or []:
                if not match.get("vulnerable", False):
                    continue
                criteria = match.get("criteria", "")
                parsed = _parse_cpe(criteria)
                if not parsed:
                    continue
                product, version = parsed
                platform = CPE_PRODUCT_PLATFORM.get(product)
                if not platform:
                    continue
                out.append({
                    "platform": platform,
                    "product": product,
                    "exact_version": version if version not in ("*", "-", "") else None,
                    "version_start_including": match.get("versionStartIncluding"),
                    "version_start_excluding": match.get("versionStartExcluding"),
                    "version_end_including": match.get("versionEndIncluding"),
                    "version_end_excluding": match.get("versionEndExcluding"),
                })
    return out


def _parse_cpe(criteria: str) -> tuple[str, str] | None:
    """Return (product, version) from a CPE 2.3 URI, or None if unparseable.

    cpe:2.3:part:vendor:product:version:update:edition:...
    """
    parts = criteria.split(":")
    if len(parts) < 6 or not criteria.startswith("cpe:2.3:"):
        return None
    return parts[4].lower(), parts[5]


def parse_cve(cve: dict) -> dict:
    """Normalise an NVD ``cve`` object into CVE model field values."""
    score, vector, severity = _best_cvss(cve)
    cve_id = cve.get("id", "")
    cpe_configs = extract_cpe_configs(cve)
    platforms = sorted({c["platform"] for c in cpe_configs})
    return {
        "cve_id": cve_id,
        "description": _english_description(cve),
        "severity": severity,
        "cvss_score": score,
        "cvss_vector": vector,
        "published_at": _parse_dt(cve.get("published")),
        "modified_at": _parse_dt(cve.get("lastModified")),
        "source_url": f"https://nvd.nist.gov/vuln/detail/{cve_id}" if cve_id else "",
        "source": "nvd",
        "affected_platforms": platforms,
        "cpe_configs": cpe_configs,
        "raw_data": {
            "published": cve.get("published"),
            "lastModified": cve.get("lastModified"),
            "vulnStatus": cve.get("vulnStatus"),
            "references": [r.get("url") for r in (cve.get("references") or [])][:20],
        },
    }


def _parse_dt(value):
    if not value:
        return None
    import datetime as _dt
    try:
        # NVD timestamps look like "2024-01-01T00:00:00.000"; tolerate a Z too.
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=_dt.timezone.utc,
        ) if "+" not in value and "Z" not in value else _dt.datetime.fromisoformat(
            value.replace("Z", "+00:00"),
        )
    except (ValueError, AttributeError):
        return None


# ── Version matching ──────────────────────────────────────────────────────────

_VER_TOKEN = re.compile(r"\d+|[a-zA-Z]+")


def parse_version(v: str) -> tuple:
    """
    Loose version → comparable tuple. Splits into numeric and alpha tokens so
    "17.3.1" < "17.3.10" and "16.12.4" < "17.3.1". Numbers sort before letters
    at the same position (so 17.3 < 17.3a). Returns () for empty/unknown.
    """
    if not v or v in ("*", "-"):
        return ()
    tokens: list[tuple] = []
    for m in _VER_TOKEN.finditer(str(v)):
        tok = m.group()
        if tok.isdigit():
            tokens.append((0, int(tok), ""))
        else:
            tokens.append((1, 0, tok.lower()))
    return tuple(tokens)


def _cmp(a: str, b: str) -> int:
    ta, tb = parse_version(a), parse_version(b)
    return (ta > tb) - (ta < tb)


def version_matches(device_version: str, config: dict) -> bool:
    """True if ``device_version`` falls within the CPE config's constraints."""
    dv = (device_version or "").strip()
    if not dv:
        return False  # unknown version → caller treats as unverified
    exact = config.get("exact_version")
    if exact:
        return _version_prefix_match(dv, exact)

    start_inc = config.get("version_start_including")
    start_exc = config.get("version_start_excluding")
    end_inc = config.get("version_end_including")
    end_exc = config.get("version_end_excluding")
    if not any([start_inc, start_exc, end_inc, end_exc]):
        return False  # no usable constraint → not a version match

    if start_inc and _cmp(dv, start_inc) < 0:
        return False
    if start_exc and _cmp(dv, start_exc) <= 0:
        return False
    if end_inc and _cmp(dv, end_inc) > 0:
        return False
    if end_exc and _cmp(dv, end_exc) >= 0:
        return False
    return True


def _version_prefix_match(device_version: str, cpe_version: str) -> bool:
    """Exact-version CPE match, tolerant of trailing-component differences.

    NVD exact CPEs are specific (e.g. 17.3.1). Treat the CPE version as a prefix
    so "17.3" in the CPE matches a device on "17.3.1"; but "17.3.1" requires the
    device to be 17.3.1(.x).
    """
    dt, ct = parse_version(device_version), parse_version(cpe_version)
    if not ct:
        return False
    if len(ct) > len(dt):
        return False
    return dt[: len(ct)] == ct


def config_constraints_apply(config: dict) -> bool:
    """Whether a config carries any version constraint we can evaluate."""
    return bool(
        config.get("exact_version")
        or config.get("version_start_including")
        or config.get("version_start_excluding")
        or config.get("version_end_including")
        or config.get("version_end_excluding"),
    )


# ── HTTP fetch ────────────────────────────────────────────────────────────────

def _resolve_api_key() -> str:
    """NVD key from CVEFeedSettings/OpenBao if set, else the env default."""
    try:
        from apps.cve.models import CVEFeedSettings
        from apps.credentials import vault

        s = CVEFeedSettings.load()
        if s.nvd_api_key_vault_path:
            secret = vault.read_secret(s.nvd_api_key_vault_path).get("nvd_api_key")
            if secret:
                return secret
    except Exception as exc:  # pragma: no cover - defensive (DB/vault hiccup)
        logger.debug("NVD key vault lookup failed, using env: %s", exc)
    return getattr(settings, "NVD_API_KEY", "") or ""


def fetch_keyword(keyword: str, *, session: requests.Session | None = None,
                  page_sleep: float | None = None) -> Iterator[dict]:
    """
    Yield raw NVD ``cve`` objects for ``keyword``, paginating to completion.

    Rate-limited per NVD guidance: 50 req/30s with an API key, 5 req/30s
    without — we sleep ~0.7s / ~6.5s between pages accordingly.
    """
    sess = session or requests.Session()
    api_key = _resolve_api_key()
    headers = {"apiKey": api_key} if api_key else {}
    sleep = page_sleep if page_sleep is not None else (0.7 if api_key else 6.5)
    page_size = min(int(getattr(settings, "NVD_RESULTS_PER_PAGE", 2000)), 2000)

    start = 0
    total = None
    while True:
        params = {
            "keywordSearch": keyword,
            "resultsPerPage": page_size,
            "startIndex": start,
        }
        resp = sess.get(NVD_URL, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        vulns = data.get("vulnerabilities", []) or []
        for v in vulns:
            cve = v.get("cve")
            if cve:
                yield cve

        total = data.get("totalResults", 0)
        fetched = start + len(vulns)
        if not vulns or fetched >= total:
            break
        start += page_size
        time.sleep(sleep)


def iter_cves(keywords: Iterable[str], **kwargs) -> Iterator[dict]:
    """Yield NVD cve objects across several keywords, de-duplicated by CVE id."""
    seen: set[str] = set()
    for kw in keywords:
        logger.info("NVD: fetching CVEs for keyword %r", kw)
        for cve in fetch_keyword(kw, **kwargs):
            cid = cve.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                yield cve
