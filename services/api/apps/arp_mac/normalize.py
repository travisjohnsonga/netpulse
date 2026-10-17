"""MAC-address normalization + OUI helpers."""
from __future__ import annotations

import re

_SEP = re.compile(r"[.:\- ]")


def normalize_mac(mac: str | None) -> str:
    """
    Normalize a MAC to lowercase colon form ``xx:xx:xx:xx:xx:xx``.

    Accepts the common vendor formats (``aabb.ccdd.eeff``, ``AA-BB-CC-DD-EE-FF``,
    ``aabbccddeeff``). Returns the input unchanged if it isn't 12 hex digits, so
    odd values are preserved rather than silently corrupted.
    """
    if not mac:
        return ""
    clean = _SEP.sub("", str(mac).strip().lower())
    if len(clean) != 12 or not re.fullmatch(r"[0-9a-f]{12}", clean):
        return str(mac).strip()
    return ":".join(clean[i:i + 2] for i in range(0, 12, 2))


def oui_of(mac: str | None) -> str:
    """First three octets of a normalized MAC (``aa:bb:cc``), or '' if invalid."""
    norm = normalize_mac(mac)
    parts = norm.split(":")
    if len(parts) != 6:
        return ""
    return ":".join(parts[:3])


# IEEE assigns 24-bit (MA-L), 28-bit (MA-M) and 36-bit (MA-S, legacy IAB)
# blocks. Prefixes are stored in the normalized MAC's own colon form, so for
# aa:bb:cc:dd:ee:ff the 28-bit prefix is "aa:bb:cc:d" and the 36-bit one
# "aa:bb:cc:dd:e" — a plain slice of the MAC. Longest first: a small block
# carved out of an MA-L wins over the MA-L ("IEEE Registration Authority").
OUI_PREFIX_LENGTHS = (13, 10, 8)

# Shown as the vendor of a locally administered MAC (U/L bit set): phone/laptop
# privacy MACs, VMs and containers. No registry assigns these, so no lookup can
# ever name a manufacturer.
LOCALLY_ADMINISTERED = "Randomized / virtual MAC"


def oui_prefixes(mac: str | None) -> list[str]:
    """Every registry prefix of a MAC, longest first, or [] if invalid."""
    norm = normalize_mac(mac)
    if len(norm.split(":")) != 6:
        return []
    return [norm[:n] for n in OUI_PREFIX_LENGTHS]


def prefix_from_assignment(assignment: str) -> str:
    """IEEE ``Assignment`` hex (6, 7 or 9 digits) → stored prefix, or ''."""
    hexstr = (assignment or "").strip().lower()
    if len(hexstr) not in (6, 7, 9) or not re.fullmatch(r"[0-9a-f]+", hexstr):
        return ""
    return ":".join(hexstr[i:i + 2] for i in range(0, len(hexstr), 2))


def is_locally_administered(mac: str | None) -> bool:
    """True when the U/L bit (0x02 of the first octet) is set."""
    norm = normalize_mac(mac)
    if len(norm.split(":")) != 6:
        return False
    return bool(int(norm[:2], 16) & 0x02)
