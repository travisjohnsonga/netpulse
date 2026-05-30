"""
Topology link discovery.

Reuses the interface discovery (which already extracts LLDP neighbor info via
SNMP or SSH) to build TopologyLink rows: for each local interface reporting an
LLDP neighbor that matches a known device, upsert a link.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def discover_links(device) -> list[dict]:
    """
    Discover this device's LLDP neighbors and persist matched links.
    Returns the discovered neighbors (matched or not). Raises DiscoveryError on
    a discovery failure (no usable credential, unreachable, …).
    """
    from django.db.models import Q

    from apps.telemetry import discovery
    from .models import Device, TopologyLink

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
            TopologyLink.objects.update_or_create(
                device_a=device, port_a=iface["if_name"],
                defaults={
                    "device_b": match,
                    "port_b": iface.get("lldp_neighbor_port") or "",
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
