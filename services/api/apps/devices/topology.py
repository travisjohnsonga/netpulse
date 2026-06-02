"""
Topology link discovery.

Reuses the interface discovery (which already extracts LLDP neighbor info via
SNMP or SSH) to build TopologyLink rows: for each local interface reporting an
LLDP neighbor that matches a known device, upsert a link.
"""
from __future__ import annotations

import logging
import re

from django.utils import timezone

logger = logging.getLogger(__name__)


# Interface-name prefix abbreviations → full form. LLDP reports the same port
# abbreviated ("Gi3") over SSH but full ("GigabitEthernet3") over SNMP; storing
# one canonical form keeps a physical link from being recorded twice.
_IFNAME_PREFIXES = {
    "gigabitethernet": "GigabitEthernet", "gige": "GigabitEthernet", "gi": "GigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet", "tengige": "TenGigabitEthernet", "te": "TenGigabitEthernet",
    "twentyfivegige": "TwentyFiveGigE",
    "fortygigabitethernet": "FortyGigabitEthernet", "fortygige": "FortyGigabitEthernet", "fo": "FortyGigabitEthernet",
    "hundredgige": "HundredGigE", "hu": "HundredGigE",
    "fastethernet": "FastEthernet", "fa": "FastEthernet",
    "ethernet": "Ethernet", "eth": "Ethernet", "et": "Ethernet",
    "port-channel": "Port-channel", "portchannel": "Port-channel", "po": "Port-channel",
    "loopback": "Loopback", "lo": "Loopback",
    "vlan": "Vlan", "vl": "Vlan",
    "tunnel": "Tunnel", "tu": "Tunnel",
    "management": "Management", "mgmt": "Management",
}


def canonical_ifname(name: str) -> str:
    """
    Canonicalise an interface name to its full form (``Gi3`` → ``GigabitEthernet3``)
    so abbreviated and full spellings dedupe to one link. Unknown prefixes are
    left as-is (trimmed); names with no numeric tail are returned trimmed.
    """
    raw = (name or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z-]*?)\s*([\d/.:]+.*)$", raw)
    if not m:
        return raw
    prefix, tail = m.group(1).lower(), m.group(2)
    full = _IFNAME_PREFIXES.get(prefix)
    return f"{full}{tail}" if full else raw


def canonical_link(dev_a, port_a, dev_b, port_b):
    """
    Order a link so the lower device id is always device_a. Both directions of
    one physical link then collapse to the same (device_a, port_a, device_b,
    port_b) tuple, which the unique constraint deduplicates.
    """
    if dev_a.id > dev_b.id:
        return dev_b, port_b, dev_a, port_a
    return dev_a, port_a, dev_b, port_b


def discover_links(device, interfaces=None) -> list[dict]:
    """
    Discover this device's LLDP neighbors and persist matched links.
    Returns the discovered neighbors (matched or not). Raises DiscoveryError on
    a discovery failure (no usable credential, unreachable, …).

    Pass `interfaces` (a discover_interfaces result) to reuse an existing scan
    and avoid a second SNMP/SSH walk; otherwise it discovers them itself.
    """
    from django.db.models import Q

    from apps.telemetry import discovery
    from .models import Device, TopologyLink

    if interfaces is None:
        interfaces = discovery.discover_interfaces(device)
    now = timezone.now()
    found = []
    for iface in interfaces:
        neighbor = iface.get("lldp_neighbor_hostname")
        mgmt_ip = iface.get("lldp_neighbor_mgmt_ip")
        if not neighbor and not mgmt_ip:
            continue
        # Match by stripped hostname (drop the domain suffix) first, then by the
        # neighbor's advertised management IP. First match wins.
        match = None
        if neighbor:
            short = neighbor.split(".")[0]
            match = (
                Device.objects.filter(hostname__iexact=short).first()
                or Device.objects.filter(hostname__iexact=neighbor).first()
            )
        if not match and mgmt_ip:
            match = Device.objects.filter(
                Q(ip_address=mgmt_ip) | Q(management_ip=mgmt_ip)
            ).first()
        if match and match.id != device.id:
            # Canonicalise interface names (Gi3 → GigabitEthernet3) so the same
            # physical link isn't stored twice when discovered via SNMP vs SSH,
            # then order so both ends map to one row.
            local_c = canonical_ifname(iface["if_name"])
            remote_c = canonical_ifname(iface.get("lldp_neighbor_port") or "")
            da, pa, db, pb = canonical_link(device, local_c, match, remote_c)
            # Drop any stale row for this same local port whose remote end differs
            # (re-cabled, or an old abbreviated-format duplicate).
            if da.id == device.id:
                stale = TopologyLink.objects.filter(device_a=device, device_b=match, port_a=local_c)
            else:
                stale = TopologyLink.objects.filter(device_a=match, device_b=device, port_b=local_c)
            stale.exclude(port_a=pa, port_b=pb).delete()
            TopologyLink.objects.update_or_create(
                device_a=da, port_a=pa, device_b=db, port_b=pb,
                defaults={
                    "discovered_via": "lldp",
                    "link_speed_mbps": iface.get("if_speed_mbps"),
                    "last_seen": now,
                },
            )
        found.append({
            "neighbor_hostname": neighbor,
            "neighbor_mgmt_ip": mgmt_ip,
            "local_port": iface["if_name"],
            "remote_port": iface.get("lldp_neighbor_port"),
            "matched_device_id": match.id if (match and match.id != device.id) else None,
        })
    return found
