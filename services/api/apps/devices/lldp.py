"""LLDP neighbor helpers: platform guessing + inventory matching.

Shared by topology.discover_links (which persists LLDPNeighbor rows) and the
/api/devices/lldp/undiscovered/ endpoint (which surfaces neighbors not yet in
inventory).
"""
from __future__ import annotations

import ipaddress
import os
import re

# Canonical LLDP capability tokens (the normalised values stored on
# LLDPNeighbor.capabilities), in display order — backs the LLDP settings UI.
KNOWN_CAPABILITIES = (
    "router", "bridge", "wlan-ap", "telephone", "station", "repeater",
    "docsis", "other",
)

# LLDP capabilities that usually denote unmanaged endpoints (IP phones, PCs,
# cable modems) rather than infrastructure worth adding to inventory. Hidden
# from the undiscovered-neighbors list by default; override per-request, via the
# LLDP_EXCLUDE_CAPABILITIES env var, or in Settings → Network Devices → LLDP
# (comma-separated; empty disables the default exclusion).
DEFAULT_UNMANAGED_CAPABILITIES = ("telephone", "station", "docsis")

# Ordered most-specific → least-specific: "Cisco IOS XE" must be tested before
# "Cisco IOS", and "ArubaOS-CX" before a bare "Aruba". The hint is matched
# case-insensitively as a substring of the neighbor's LLDP system-description.
# Values are valid Device.Platform choices so the result can pre-fill the Add
# Device form without further translation.
PLATFORM_HINTS: list[tuple[str, str]] = [
    ("Cisco IOS XE", "ios_xe"),
    ("Cisco IOS-XE", "ios_xe"),
    ("IOS-XE", "ios_xe"),
    ("Cisco IOS XR", "ios_xr"),
    ("Cisco IOS-XR", "ios_xr"),
    ("IOS-XR", "ios_xr"),
    ("NX-OS", "nxos"),
    ("Nexus", "nxos"),
    ("Cisco IOS", "ios"),
    ("ArubaOS-CX", "aos_cx"),
    ("AOS-CX", "aos_cx"),
    ("Aruba", "aos_cx"),
    ("Arista", "eos"),
    ("FortiOS", "fortios"),
    ("Fortinet", "fortios"),
    ("FortiGate", "fortios"),
    ("Juniper", "junos"),
    ("JUNOS", "junos"),
    ("SonicOS", "sonicwall"),
    ("SonicWall", "sonicwall"),
    ("PAN-OS", "panos"),
    ("Palo Alto", "panos"),
    ("UniFi", "unifi_sw"),
    ("Ubiquiti", "unifi_sw"),
]


def guess_platform(system_description: str | None) -> str:
    """Best-effort Device.Platform from an LLDP system-description string.

    Returns ``'other'`` when nothing matches (a valid Platform choice, so the
    guess is always safe to drop into the Add Device form).
    """
    desc = (system_description or "").lower()
    if not desc:
        return "other"
    for hint, platform in PLATFORM_HINTS:
        if hint.lower() in desc:
            return platform
    return "other"


_MAC_RE = re.compile(r"^([0-9a-f]{2}[:\-]){5}[0-9a-f]{2}$", re.IGNORECASE)
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def infer_chassis_id_type(chassis_id: str | None) -> str:
    """Infer the chassis-id subtype from its value (mac / network-address)."""
    cid = (chassis_id or "").strip()
    if _MAC_RE.match(cid):
        return "mac"
    if _IP_RE.match(cid):
        return "network-address"
    return ""


def valid_ip(value: str | None) -> bool:
    """True if ``value`` parses as an IPv4/IPv6 address.

    LLDP neighbours sometimes advertise a MAC in a management-address field;
    callers use this to keep such values out of inet columns. An optional CIDR
    suffix is tolerated. Rejects MACs (6 hex groups is never a valid IPv6).
    """
    try:
        ipaddress.ip_address(str(value or "").strip().split("/", 1)[0])
        return True
    except ValueError:
        return False


# Single-letter LLDP capability codes (e.g. from an SNMP-style "B, R" string).
_CAP_CODES = {
    "b": "bridge", "r": "router", "w": "wlan-ap", "a": "wlan-ap",
    "t": "telephone", "c": "docsis", "s": "station", "o": "other",
    "p": "repeater", "d": "docsis",
}

# Full-name variants (AOS-CX dict keys, vendor strings, OpenConfig names) →
# canonical token, so the same capability lands on one token regardless of how
# the platform spells it (e.g. "wlan-access-point" and code "w" both → wlan-ap).
_CAP_ALIASES = {
    "mac-bridge": "bridge", "mac_bridge": "bridge",
    "wlan-access-point": "wlan-ap", "wlan_access_point": "wlan-ap",
    "wlan-ap": "wlan-ap", "access-point": "wlan-ap", "accesspoint": "wlan-ap",
    "station-only": "station",
    "docsis-cable-device": "docsis", "cable": "docsis",
    "phone": "telephone",
}


def normalize_capabilities(raw) -> list[str]:
    """Normalise LLDP capabilities to a canonical lowercase token list.

    Accepts a list already, a delimited string like ``"B, R"`` /
    ``"bridge router"``, or a dict keyed by capability with truthy values
    (e.g. AOS-CX's ``{"bridge": True, "router": False}``). Single-letter codes
    are expanded and full-name spelling variants (``wlan-access-point`` →
    ``wlan-ap``, ``mac-bridge`` → ``bridge``, …) are folded to one token so the
    UI capability filters match across every platform.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [k for k, v in raw.items() if v]
    if isinstance(raw, (list, tuple)):
        tokens = [str(t).strip() for t in raw]
    else:
        tokens = re.split(r"[,;/\s]+", str(raw))
    out: list[str] = []
    for tok in tokens:
        t = tok.strip().lower()
        if not t:
            continue
        t = _CAP_CODES.get(t) or _CAP_ALIASES.get(t, t)
        if t not in out:
            out.append(t)
    return out


def device_identity_index(devices) -> tuple[set[str], set[str]]:
    """Build (hostnames, ips) lookup sets for fast in-inventory checks.

    `hostnames` holds each device hostname both full and domain-stripped, lower
    case. `ips` holds each device's ip_address and management_ip.
    """
    hostnames: set[str] = set()
    ips: set[str] = set()
    for d in devices:
        if d.hostname:
            hn = d.hostname.lower()
            hostnames.add(hn)
            hostnames.add(hn.split(".")[0])
        if d.ip_address:
            ips.add(str(d.ip_address))
        if d.management_ip:
            ips.add(str(d.management_ip))
    return hostnames, ips


def neighbor_in_inventory(neighbor, hostnames: set[str], ips: set[str]) -> bool:
    """True if this LLDPNeighbor maps to a known device (live re-check).

    Used at query time so a neighbor added to inventory *after* the last LLDP
    scan still drops off the undiscovered list. Matches by management address,
    chassis-id (when it is an IP), or system name (full or domain-stripped).
    """
    if neighbor.matched_device_id:
        return True
    if neighbor.management_address and str(neighbor.management_address) in ips:
        return True
    cid = (neighbor.chassis_id or "").strip()
    if cid and _IP_RE.match(cid) and cid in ips:
        return True
    name = (neighbor.system_name or "").strip().lower()
    if name and (name in hostnames or name.split(".")[0] in hostnames):
        return True
    return False


def default_excluded_capabilities() -> list[str]:
    """Capabilities excluded from the undiscovered list unless a request overrides them.

    Precedence: the admin-configurable ``lldp_exclude_capabilities``
    SystemSetting (settable from Settings → Network Devices → LLDP; an empty
    value means exclude nothing), then the ``LLDP_EXCLUDE_CAPABILITIES`` env
    default, then :data:`DEFAULT_UNMANAGED_CAPABILITIES`. Resilient to DB errors
    during early migration so the endpoint never breaks.
    """
    raw = None
    try:
        from apps.core.models import SystemSetting

        raw = SystemSetting.get("lldp_exclude_capabilities", None)
    except Exception:  # noqa: BLE001 — never break the endpoint on a DB issue
        raw = None
    if raw is None:
        raw = os.environ.get("LLDP_EXCLUDE_CAPABILITIES")
    if raw is None:
        return list(DEFAULT_UNMANAGED_CAPABILITIES)
    return [c.strip().lower() for c in raw.split(",") if c.strip()]


def filter_undiscovered(neighbors, *, search="", include_caps=None,
                        exclude_caps=None, has_ip=None, platforms=None):
    """Filter a list of undiscovered LLDPNeighbor rows (in Python).

    capabilities is a JSONField (not a Postgres array), so filtering happens
    after the query rather than in SQL.

    - ``search``: case-insensitive substring over system_name / management
      address / chassis_id / observing device hostname.
    - ``include_caps``: keep only neighbors advertising at least one of these.
    - ``exclude_caps``: drop neighbors advertising any of these.
    - ``has_ip``: True → only with a management address; False → only without.
    - ``platforms``: keep only neighbors whose guessed platform is in this set.

    Neighbors advertising NO capabilities are always kept past the include /
    exclude caps filters — an unknown device could be anything, so it stays
    visible rather than being silently dropped.
    """
    inc = {c.lower() for c in (include_caps or [])}
    exc = {c.lower() for c in (exclude_caps or [])}
    plats = {p.lower() for p in (platforms or [])}
    q = (search or "").strip().lower()
    out = []
    for n in neighbors:
        caps = {c.lower() for c in (n.capabilities or [])}
        if caps:
            if inc and not (caps & inc):
                continue
            if exc and (caps & exc):
                continue
        if has_ip is True and not n.management_address:
            continue
        if has_ip is False and n.management_address:
            continue
        if plats and guess_platform(n.system_description).lower() not in plats:
            continue
        if q:
            host = n.seen_by.hostname if n.seen_by_id else ""
            hay = " ".join((
                n.system_name or "", str(n.management_address or ""),
                n.chassis_id or "", host or "",
            )).lower()
            if q not in hay:
                continue
        out.append(n)
    return out
