"""
Best-effort connectivity probe for the credential "test" endpoint.

This verifies *reachability* of the relevant service port for a profile against
a given IP — a fast, dependency-light check suitable for the UI "Test" button.
Full protocol-level authentication (an actual SNMP GET / SSH login) is left to
the poller/ingest layer, which already holds the live protocol stacks; doing it
here would pull heavy deps into the API container.
"""
from __future__ import annotations

import logging
import socket
import time

logger = logging.getLogger(__name__)

# Default service port per credential type.
DEFAULT_PORTS = {
    "snmpv1": 161, "snmpv2c": 161, "snmpv3": 161,
    "ssh_password": 22, "ssh_key": 22,
    "netconf": 830,
    "gnmi": 50051,
    "http_basic": 443, "http_token": 443, "http_apikey": 443,
}

# Credential types that speak UDP (SNMP) — a TCP connect test doesn't apply.
_UDP_TYPES = {"snmpv1", "snmpv2c", "snmpv3"}


def _resolve_port(credential_type: str, port: int | None, tls_enabled: bool) -> int:
    if port:
        return port
    if credential_type in ("http_basic", "http_token", "http_apikey"):
        return 443 if tls_enabled else 80
    return DEFAULT_PORTS.get(credential_type, 0)


def probe(credential_type: str, ip: str, port: int | None, tls_enabled: bool,
          timeout: float = 3.0) -> dict:
    """
    Return ``{"success": bool, "message": str, "latency_ms": int|None,
    "port": int}``.  Never raises.
    """
    resolved = _resolve_port(credential_type, port, tls_enabled)
    if not resolved:
        return {"success": False, "message": "No port to probe for this credential type.",
                "latency_ms": None, "port": 0}

    start = time.monotonic()

    if credential_type in _UDP_TYPES:
        # UDP is connectionless — we can only confirm we can send a datagram.
        # A real SNMP GET happens on the poller. Report the intent honestly.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(b"\x00", (ip, resolved))
            s.close()
            return {"success": True,
                    "message": f"UDP/{resolved} datagram sent to {ip}. "
                               "Full SNMP auth is verified by the poller.",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "port": resolved}
        except OSError as exc:
            logger.info("UDP probe to %s:%s failed: %s", ip, resolved, exc)
            return {"success": False, "message": f"UDP/{resolved} send to {ip} failed.",
                    "latency_ms": None, "port": resolved}

    # TCP connect test for SSH / NETCONF / gNMI / HTTP.
    try:
        with socket.create_connection((ip, resolved), timeout=timeout):
            return {"success": True,
                    "message": f"TCP/{resolved} reachable on {ip}.",
                    "latency_ms": int((time.monotonic() - start) * 1000),
                    "port": resolved}
    except OSError as exc:
        logger.info("TCP probe to %s:%s failed: %s", ip, resolved, exc)
        return {"success": False,
                "message": f"TCP/{resolved} on {ip} is unreachable.",
                "latency_ms": None, "port": resolved}
