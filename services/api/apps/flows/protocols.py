"""IP-protocol-number and TCP/UDP-port helper lookups for flow records.

NetFlow/sFlow records carry the numeric ``ip_protocol`` (IANA protocol number)
and raw L4 ports. These maps turn those numbers into the human-readable names
the Flow Analytics UI shows (TCP/UDP/ICMP, HTTPS/SSH/DNS, …).
"""
from __future__ import annotations

# IANA IP protocol number → name (the handful that actually turn up in flows).
PROTOCOL_NAMES: dict[int, str] = {
    1: "ICMP",
    2: "IGMP",
    6: "TCP",
    17: "UDP",
    41: "IPv6",
    47: "GRE",
    50: "ESP",
    51: "AH",
    58: "ICMPv6",
    88: "EIGRP",
    89: "OSPF",
    103: "PIM",
    112: "VRRP",
    132: "SCTP",
}

# Reverse map for the ?protocol=tcp|udp|icmp filter (name → number).
PROTOCOL_NUMBERS: dict[str, int] = {name.lower(): num for num, name in PROTOCOL_NAMES.items()}

# Well-known TCP/UDP service ports → service name.
SERVICE_PORTS: dict[int, str] = {
    20: "FTP-DATA",
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    67: "DHCP",
    68: "DHCP",
    69: "TFTP",
    80: "HTTP",
    110: "POP3",
    111: "RPC",
    123: "NTP",
    135: "MS-RPC",
    137: "NetBIOS",
    138: "NetBIOS",
    139: "NetBIOS",
    143: "IMAP",
    161: "SNMP",
    162: "SNMP-Trap",
    179: "BGP",
    389: "LDAP",
    443: "HTTPS",
    445: "SMB",
    465: "SMTPS",
    500: "IKE",
    514: "Syslog",
    515: "LPD",
    520: "RIP",
    546: "DHCPv6",
    547: "DHCPv6",
    587: "SMTP",
    636: "LDAPS",
    993: "IMAPS",
    995: "POP3S",
    1433: "MSSQL",
    1521: "Oracle",
    1812: "RADIUS",
    1813: "RADIUS",
    2055: "NetFlow",
    3306: "MySQL",
    3389: "RDP",
    4444: "SonicWall-Mgmt",
    5060: "SIP",
    5061: "SIP-TLS",
    5432: "PostgreSQL",
    6343: "sFlow",
    6443: "Kubernetes",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
    9200: "OpenSearch",
}


def protocol_name(num) -> str:
    """Return the protocol name for an IP protocol number (or ``Proto N`` / the
    raw string when unknown / unparsable)."""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return str(num) if num not in (None, "") else "—"
    return PROTOCOL_NAMES.get(n, f"Proto {n}")


def service_name(port) -> str | None:
    """Return the well-known service name for a port, or None if not recognised."""
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return SERVICE_PORTS.get(p)
