"""MAC → vendor resolution against the locally stored IEEE registry.

Lookups never leave the box: the full registry is downloaded (or loaded from
the bundled snapshot) by ``update_mac_vendors``, so no MAC or OUI is ever sent
to a third party.
"""
from __future__ import annotations

from .models import MACVendor
from .normalize import LOCALLY_ADMINISTERED, is_locally_administered, oui_prefixes


def vendor_map(macs) -> dict:
    """Resolve {mac: vendor} for a set of MACs in one query (longest prefix wins)."""
    macs = [m for m in macs if m]
    prefixes = {p for m in macs for p in oui_prefixes(m)}
    lookup = (dict(MACVendor.objects.filter(oui__in=prefixes).values_list("oui", "vendor"))
              if prefixes else {})
    return {m: _resolve(m, lookup) for m in macs}


def vendor_for(mac: str) -> str:
    return vendor_map([mac]).get(mac, "")


def _resolve(mac: str, lookup: dict) -> str:
    for prefix in oui_prefixes(mac):
        if prefix in lookup:
            return lookup[prefix]
    return LOCALLY_ADMINISTERED if is_locally_administered(mac) else ""
