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


def _norm_link(a_id, port_a, b_id, port_b, speed):
    """Order a link's endpoints so the lower device id is first (so both
    directions of one physical link collapse to the same tuple)."""
    if a_id > b_id:
        return (b_id, port_b or "", a_id, port_a or "", speed)
    return (a_id, port_a or "", b_id, port_b or "", speed)


def build_edges(topology_links, lldp_neighbors, dev_ids) -> list[dict]:
    """Aggregate physical links between device pairs into one edge each.

    Combines discovered TopologyLink rows with LLDP-neighbor matches: a device
    the fleet *sees* via LLDP but that doesn't report LLDP itself (e.g. a UniFi
    AP, which is seen by its uplink switch) still gets an edge. Parallel links
    between the same pair (LAG / redundant uplinks) collapse into one edge
    carrying ``link_count`` + per-link port detail, so the map draws a single
    line with an "x2"/"x3" badge instead of overlapping duplicates.
    """
    from collections import defaultdict

    raw = []  # (low_id, port_low, high_id, port_high, speed)
    for ln in topology_links:
        if ln.device_a_id in dev_ids and ln.device_b_id in dev_ids:
            raw.append(_norm_link(ln.device_a_id, ln.port_a, ln.device_b_id,
                                  ln.port_b, ln.link_speed_mbps))
    for nb in lldp_neighbors:
        a, b = nb.seen_by_id, nb.matched_device_id
        if not b or a == b or a not in dev_ids or b not in dev_ids:
            continue
        raw.append(_norm_link(a, nb.local_interface, b, nb.port_id, None))

    # Group by device pair, deduping links by their canonical port pair so a
    # link present in BOTH a TopologyLink and an LLDP match isn't double-counted.
    groups: dict = defaultdict(dict)
    for low, port_low, high, port_high, speed in raw:
        key = (canonical_ifname(port_low), canonical_ifname(port_high))
        link = groups[(low, high)].setdefault(
            key, {"port_a": port_low, "port_b": port_high, "speed_mbps": speed})
        if speed and not link["speed_mbps"]:
            link["speed_mbps"] = speed

    edges = []
    for (low, high), links in groups.items():
        link_list = list(links.values())
        speeds = [ln["speed_mbps"] for ln in link_list if ln["speed_mbps"]]
        edges.append({
            "source": str(low), "target": str(high),
            "link_count": len(link_list),
            "label": f"x{len(link_list)}" if len(link_list) > 1 else "",
            "links": link_list,
            "speed_mbps": max(speeds) if speeds else None,
            # First link's ports kept at top level for back-compat / simple labels.
            "port_a": link_list[0]["port_a"], "port_b": link_list[0]["port_b"],
        })
    return edges


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
    from . import lldp
    from .models import Device, LLDPNeighbor, TopologyLink

    if interfaces is None:
        interfaces = discovery.discover_interfaces(device)
    now = timezone.now()
    found = []
    for iface in interfaces:
        neighbor = iface.get("lldp_neighbor_hostname")
        mgmt_ip = iface.get("lldp_neighbor_mgmt_ip")
        # Some neighbours advertise a MAC (or other junk) where an IP belongs;
        # drop it so it can't crash the management_address inet write and abort
        # the whole device's collection.
        if mgmt_ip and not lldp.valid_ip(mgmt_ip):
            mgmt_ip = None
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
        # Persist the raw neighbor (matched or not) so the "LLDP Neighbors — Not
        # in Inventory" page can surface discovery gaps. Keyed per local port.
        matched = match if (match and match.id != device.id) else None
        chassis_id = iface.get("lldp_neighbor_chassis_id") or ""
        LLDPNeighbor.objects.update_or_create(
            seen_by=device,
            local_interface=iface["if_name"],
            defaults={
                "chassis_id": chassis_id,
                "chassis_id_type": iface.get("lldp_neighbor_chassis_type")
                or lldp.infer_chassis_id_type(chassis_id),
                "port_id": iface.get("lldp_neighbor_port") or "",
                "port_description": iface.get("lldp_neighbor_desc") or "",
                "system_name": neighbor or "",
                "system_description": iface.get("lldp_neighbor_system_desc") or "",
                "management_address": mgmt_ip or None,
                "capabilities": lldp.normalize_capabilities(
                    iface.get("lldp_neighbor_capabilities")),
                "matched_device": matched,
                "last_seen": now,
            },
        )
        # Stamp first_seen once, on initial sighting.
        LLDPNeighbor.objects.filter(
            seen_by=device, local_interface=iface["if_name"], first_seen__isnull=True
        ).update(first_seen=now)
        found.append({
            "neighbor_hostname": neighbor,
            "neighbor_mgmt_ip": mgmt_ip,
            "local_port": iface["if_name"],
            "remote_port": iface.get("lldp_neighbor_port"),
            "matched_device_id": matched.id if matched else None,
        })
    # Drop neighbor rows for local ports that no longer advertise an LLDP
    # neighbor (re-cabled, neighbor removed, port shut). Only reached on a
    # successful scan — discover_interfaces raises DiscoveryError on a
    # collection failure, so a transient outage never wipes the table.
    seen_local = [f["local_port"] for f in found]
    LLDPNeighbor.objects.filter(seen_by=device).exclude(
        local_interface__in=seen_local).delete()
    return found


def collect_all_lldp(devices=None) -> dict:
    """Refresh LLDP neighbors across the fleet, persisting LLDPNeighbor rows.

    Runs discover_links per device. Defaults to reachable active devices; pass an
    explicit queryset/iterable to override. Per-device collection failures are
    logged and skipped so one unreachable device never aborts the sweep. Returns
    a ``{devices, neighbors, failed}`` summary.
    """
    from apps.telemetry.discovery import DiscoveryError

    from .models import Device

    if devices is None:
        devices = Device.objects.filter(
            status=Device.Status.ACTIVE, is_reachable=True)
    summary = {"devices": 0, "neighbors": 0, "failed": 0}
    for device in devices:
        summary["devices"] += 1
        try:
            found = discover_links(device)
            summary["neighbors"] += len(found)
        except DiscoveryError as exc:
            summary["failed"] += 1
            logger.debug("LLDP collection failed for %s: %s", device.hostname, exc)
        except Exception as exc:  # noqa: BLE001 — one bad device mustn't abort the sweep
            summary["failed"] += 1
            logger.warning("LLDP collection error for %s: %s", device.hostname, exc)
    logger.info("LLDP collection: %d device(s), %d neighbor(s), %d failed",
                summary["devices"], summary["neighbors"], summary["failed"])
    return summary
