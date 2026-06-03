"""
Cisco PSIRT openVuln API (optional).

Pulls Cisco security advisories for the Cisco OS types present in inventory and
normalises them into CVE field dicts (source="cisco_psirt"). Entirely optional:
``fetch_advisories`` yields nothing unless a PSIRT client id + secret are
configured (settings or OpenBao). Auth is OAuth2 client-credentials.

PSIRT advisories carry CVE ids + a Security Impact Rating but not machine CPE
version ranges in a form we version-match, so correlation treats these as
platform/keyword matches.
"""
from __future__ import annotations

import logging
from typing import Iterable, Iterator

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
ADVISORY_OS_URL = "https://apix.cisco.com/security/advisories/v2/OSType/{os_type}"

# NetPulse platform → Cisco PSIRT OSType path token.
PLATFORM_OS_TYPE = {
    "ios": "ios",
    "ios_xe": "iosxe",
    "ios_xr": "iosxr",
    "nxos": "nxos",
}

_SIR_SEVERITY = {
    "Critical": "critical", "High": "high", "Medium": "medium", "Low": "low",
    "Informational": "none",
}


def _credentials() -> tuple[str, str]:
    """(client_id, client_secret) from OpenBao feed settings, else env."""
    try:
        from apps.credentials import vault

        from .models import CVEFeedSettings
        s = CVEFeedSettings.load()
        if s.cisco_psirt_client_id_vault_path:
            secret = vault.read_secret(s.cisco_psirt_client_id_vault_path)
            cid, csecret = secret.get("client_id"), secret.get("client_secret")
            if cid and csecret:
                return cid, csecret
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("PSIRT vault lookup failed, using env: %s", exc)
    return (
        getattr(settings, "CISCO_PSIRT_CLIENT_ID", "") or "",
        getattr(settings, "CISCO_PSIRT_CLIENT_SECRET", "") or "",
    )


def _get_token(client_id: str, client_secret: str, session: requests.Session) -> str:
    resp = session.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _parse_advisory(adv: dict, platform: str) -> list[dict]:
    cves = adv.get("cves") or []
    severity = _SIR_SEVERITY.get(adv.get("sir", ""), "none")
    score = adv.get("cvssBaseScore")
    try:
        score = float(score) if score not in (None, "NA", "") else None
    except (TypeError, ValueError):
        score = None
    url = adv.get("publicationUrl") or ""
    title = adv.get("advisoryTitle") or adv.get("summary") or adv.get("advisoryId") or ""
    out = []
    for cid in cves:
        if not isinstance(cid, str) or not cid.startswith("CVE-"):
            continue
        out.append({
            "cve_id": cid,
            "description": title,
            "severity": severity,
            "cvss_score": score,
            "cvss_vector": "",
            "published_at": None,
            "modified_at": None,
            "source_url": url,
            "source": "cisco_psirt",
            "affected_platforms": [platform],
            "cpe_configs": [],
            "raw_data": {"advisory_id": adv.get("advisoryId"), "sir": adv.get("sir")},
        })
    return out


def fetch_advisories(platforms: Iterable[str], *,
                     session: requests.Session | None = None) -> Iterator[dict]:
    """Yield normalised CVE dicts from Cisco PSIRT for the Cisco platforms given."""
    client_id, client_secret = _credentials()
    if not (client_id and client_secret):
        logger.info("Cisco PSIRT not configured — skipping")
        return
    os_types = {PLATFORM_OS_TYPE[p]: p for p in platforms if p in PLATFORM_OS_TYPE}
    if not os_types:
        return

    sess = session or requests.Session()
    token = _get_token(client_id, client_secret, sess)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    for os_type, platform in os_types.items():
        try:
            resp = sess.get(ADVISORY_OS_URL.format(os_type=os_type), headers=headers, timeout=60)
            resp.raise_for_status()
            advisories = resp.json().get("advisories", []) or []
        except Exception as exc:  # pragma: no cover - best-effort per OS
            logger.warning("PSIRT fetch failed for %s: %s", os_type, exc)
            continue
        for adv in advisories:
            yield from _parse_advisory(adv, platform)
