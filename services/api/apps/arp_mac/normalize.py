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
