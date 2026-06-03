"""
CISA Known Exploited Vulnerabilities (KEV) feed.

Free, no-auth daily JSON of CVEs known to be exploited in the wild. We use it to
flag matching CVE rows (``cisa_kev=True``) so the UI can surface them as the
highest priority — it never creates new CVE rows on its own.
"""
from __future__ import annotations

import logging

import requests

from .models import CVE

logger = logging.getLogger(__name__)

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def fetch_kev_ids(*, session: requests.Session | None = None) -> set[str]:
    sess = session or requests.Session()
    resp = sess.get(KEV_URL, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return {v["cveID"] for v in data.get("vulnerabilities", []) if v.get("cveID")}


def flag_known_exploited(*, session: requests.Session | None = None) -> int:
    """
    Set ``cisa_kev=True`` on CVE rows that appear on the KEV list. Returns the
    number of rows newly flagged. Only touches CVEs we already track.
    """
    kev_ids = fetch_kev_ids(session=session)
    if not kev_ids:
        return 0
    flagged = (
        CVE.objects.filter(cve_id__in=kev_ids, cisa_kev=False).update(cisa_kev=True)
    )
    return flagged
