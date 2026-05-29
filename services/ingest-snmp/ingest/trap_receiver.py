"""
SNMP trap / SNMPv3 inform receiver.

Uses asyncio.DatagramProtocol for UDP and pysnmp's BER decoder to parse
SNMPv1 (TrapPDU) and SNMPv2c (V2 Trap-PDU) messages.

SNMPv3 informs require USM (User-based Security Model) decryption / MAC
verification which demands a pre-provisioned user database.  That layer is
stubbed here with a TODO; the raw packet is still delivered to NATS with a
best-effort decode of the outer header.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from .mib_resolver import display_name, resolve
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)

# SNMPv1 generic-trap → SNMPv2 notification OID mapping
_V1_GENERIC_OID = {
    0: "1.3.6.1.6.3.1.1.5.1",  # coldStart
    1: "1.3.6.1.6.3.1.1.5.2",  # warmStart
    2: "1.3.6.1.6.3.1.1.5.3",  # linkDown
    3: "1.3.6.1.6.3.1.1.5.4",  # linkUp
    4: "1.3.6.1.6.3.1.1.5.5",  # authenticationFailure
    5: "1.3.6.1.6.3.1.1.5.6",  # egpNeighborLoss
}

_V1_GENERIC_NAME = {
    0: "coldStart", 1: "warmStart", 2: "linkDown", 3: "linkUp",
    4: "authenticationFailure", 5: "egpNeighborLoss", 6: "enterpriseSpecific",
}


# ── public asyncio protocol ───────────────────────────────────────────────────

class SNMPTrapReceiver(asyncio.DatagramProtocol):
    def __init__(self, publisher: NATSPublisher) -> None:
        self._publisher = publisher
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport
        addr = transport.get_extra_info("sockname")
        logger.info("SNMP trap receiver listening on %s:%d/udp", addr[0], addr[1])

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        asyncio.create_task(self._handle(data, addr[0], addr[1]))

    async def _handle(self, data: bytes, source_ip: str, source_port: int) -> None:
        trap = decode_trap(data, source_ip, source_port)
        if trap is None:
            logger.debug("failed to decode trap from %s", source_ip)
            return
        device_id = trap.get("agent_addr") or source_ip
        await self._publisher.publish_trap(device_id, trap)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP error: %s", exc)


# ── decoder ───────────────────────────────────────────────────────────────────

def decode_trap(data: bytes, source_ip: str, source_port: int) -> dict[str, Any] | None:
    """
    Decode a raw UDP packet into a trap dict.
    Returns None if the data cannot be parsed as SNMP.
    """
    try:
        from pysnmp.proto import api as pMod
        from pyasn1.codec.ber import decoder as ber_decoder
    except ImportError:
        logger.error("pysnmp/pyasn1 not installed — trap decoding unavailable")
        return None

    try:
        msg_version = int(pMod.decodeMessageVersion(data))
    except Exception as exc:
        logger.debug("cannot detect SNMP version: %s", exc)
        return None

    now = datetime.now(timezone.utc).isoformat()

    if msg_version == pMod.protoVersion1:
        return _decode_v1(data, source_ip, source_port, now, pMod, ber_decoder)
    if msg_version == pMod.protoVersion2c:
        return _decode_v2c(data, source_ip, source_port, now, pMod, ber_decoder)
    # SNMPv3 — USM decryption/auth not yet implemented
    return _decode_v3_stub(data, source_ip, source_port, now, msg_version)


def _decode_v1(data, source_ip, source_port, now, pMod, ber_decoder) -> dict | None:
    v1 = pMod.protoModules[pMod.protoVersion1]
    try:
        msg, _ = ber_decoder.decode(data, asn1Spec=v1.Message())
    except Exception as exc:
        logger.debug("v1 BER decode failed: %s", exc)
        return None

    try:
        community = str(v1.apiMessage.getCommunity(msg))
        pdu = v1.apiMessage.getPDU(msg)
        enterprise = str(v1.apiTrapPDU.getEnterprise(pdu))
        agent_addr = str(v1.apiTrapPDU.getAgentAddr(pdu)) or source_ip
        generic = int(v1.apiTrapPDU.getGenericTrap(pdu))
        specific = int(v1.apiTrapPDU.getSpecificTrap(pdu))
        uptime = int(v1.apiTrapPDU.getTimeStamp(pdu))
        varbinds = _decode_varbinds(v1.apiTrapPDU.getVarBinds(pdu))
    except Exception as exc:
        logger.debug("v1 PDU field extraction failed: %s", exc)
        return None

    trap_oid = _V1_GENERIC_OID.get(generic) if generic < 6 else f"{enterprise}.0.{specific}"

    return {
        "received_at": now,
        "source_ip": source_ip,
        "source_port": source_port,
        "version": "v1",
        "community": community,
        "agent_addr": agent_addr,
        "enterprise": enterprise,
        "generic_trap": generic,
        "generic_trap_name": _V1_GENERIC_NAME.get(generic, "enterpriseSpecific"),
        "specific_trap": specific,
        "uptime_hundredths": uptime,
        "trap_oid": trap_oid,
        "trap_name": display_name(trap_oid) if trap_oid else None,
        "varbinds": varbinds,
    }


def _decode_v2c(data, source_ip, source_port, now, pMod, ber_decoder) -> dict | None:
    v2c = pMod.protoModules[pMod.protoVersion2c]
    try:
        msg, _ = ber_decoder.decode(data, asn1Spec=v2c.Message())
    except Exception as exc:
        logger.debug("v2c BER decode failed: %s", exc)
        return None

    try:
        community = str(v2c.apiMessage.getCommunity(msg))
        pdu = v2c.apiMessage.getPDU(msg)
        varbinds = _decode_varbinds(v2c.apiPDU.getVarBinds(pdu))
    except Exception as exc:
        logger.debug("v2c PDU field extraction failed: %s", exc)
        return None

    # First two varbinds are sysUpTime.0 and snmpTrapOID.0 (RFC 3416)
    uptime = None
    trap_oid = None
    for vb in varbinds:
        if vb["oid"] == "1.3.6.1.2.1.1.3.0":
            uptime = vb["value"]
        elif vb["oid"] == "1.3.6.1.6.3.1.1.4.1.0":
            trap_oid = vb["value"]

    return {
        "received_at": now,
        "source_ip": source_ip,
        "source_port": source_port,
        "version": "v2c",
        "community": community,
        "agent_addr": source_ip,
        "enterprise": None,
        "generic_trap": None,
        "generic_trap_name": None,
        "specific_trap": None,
        "uptime_hundredths": uptime,
        "trap_oid": trap_oid,
        "trap_name": display_name(trap_oid) if trap_oid else None,
        "varbinds": varbinds,
    }


def _decode_v3_stub(data, source_ip, source_port, now, msg_version) -> dict:
    # TODO: implement USM decryption/authentication for SNMPv3 informs.
    # Requires pre-provisioned engine ID + user credentials from OpenBao.
    logger.info("SNMPv3 inform from %s — USM decoding not yet implemented", source_ip)
    return {
        "received_at": now,
        "source_ip": source_ip,
        "source_port": source_port,
        "version": "v3",
        "community": None,
        "agent_addr": source_ip,
        "enterprise": None,
        "generic_trap": None,
        "generic_trap_name": None,
        "specific_trap": None,
        "uptime_hundredths": None,
        "trap_oid": None,
        "trap_name": None,
        "varbinds": [],
        "_raw_len": len(data),
        "_note": "SNMPv3 USM decoding not yet implemented",
    }


def _decode_varbinds(raw) -> list[dict[str, Any]]:
    result = []
    for oid_obj, val_obj in raw:
        oid = str(oid_obj)
        try:
            value = val_obj.prettyPrint()
            type_name = type(val_obj).__name__
        except Exception:
            value = repr(val_obj)
            type_name = "Unknown"
        mib, name, instance = resolve(oid)
        result.append({
            "oid": oid,
            "name": f"{name}.{instance}" if instance else name,
            "mib": mib,
            "value": value,
            "type": type_name,
        })
    return result
