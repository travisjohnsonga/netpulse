"""
OID → human-readable name resolver.

Uses a static table covering the four MIBs specified in the project brief:
  • IF-MIB    (RFC 2863) — interface counters and status
  • BGP4-MIB  (RFC 1657) — BGP peer state and statistics
  • UPS-MIB   (RFC 1628) — UPS battery, input, output, alarms
  • APC-POWERNET-MIB     — APC SmartUPS proprietary objects

Resolution is prefix-based: OID 1.3.6.1.2.1.2.2.1.10.5 resolves to
(IF-MIB, ifInOctets, 5).  Unknown OIDs return ("unknown", oid, "").
"""
from __future__ import annotations

# Maps OID prefix → (MIB name, object name)
# Scalar OIDs (ending .0) map their bare prefix; table column OIDs map
# the column OID without the instance suffix.
_TABLE: dict[str, tuple[str, str]] = {

    # ── SNMPv2-MIB / system ──────────────────────────────────────────────────
    "1.3.6.1.2.1.1.1":       ("SNMPv2-MIB", "sysDescr"),
    "1.3.6.1.2.1.1.2":       ("SNMPv2-MIB", "sysObjectID"),
    "1.3.6.1.2.1.1.3":       ("SNMPv2-MIB", "sysUpTime"),
    "1.3.6.1.2.1.1.4":       ("SNMPv2-MIB", "sysContact"),
    "1.3.6.1.2.1.1.5":       ("SNMPv2-MIB", "sysName"),
    "1.3.6.1.2.1.1.6":       ("SNMPv2-MIB", "sysLocation"),
    "1.3.6.1.2.1.1.7":       ("SNMPv2-MIB", "sysServices"),
    # snmpTrap variables
    "1.3.6.1.2.1.1.3.0":     ("SNMPv2-MIB", "sysUpTime.0"),
    "1.3.6.1.6.3.1.1.4.1.0": ("SNMPv2-MIB", "snmpTrapOID.0"),
    "1.3.6.1.6.3.1.1.4.3.0": ("SNMPv2-MIB", "snmpTrapEnterprise.0"),
    # Standard SNMPv2 notification OIDs
    "1.3.6.1.6.3.1.1.5.1":   ("SNMPv2-MIB", "coldStart"),
    "1.3.6.1.6.3.1.1.5.2":   ("SNMPv2-MIB", "warmStart"),
    "1.3.6.1.6.3.1.1.5.3":   ("SNMPv2-MIB", "linkDown"),
    "1.3.6.1.6.3.1.1.5.4":   ("SNMPv2-MIB", "linkUp"),
    "1.3.6.1.6.3.1.1.5.5":   ("SNMPv2-MIB", "authenticationFailure"),
    "1.3.6.1.6.3.1.1.5.6":   ("SNMPv2-MIB", "egpNeighborLoss"),

    # ── IF-MIB (RFC 2863) ────────────────────────────────────────────────────
    "1.3.6.1.2.1.2.1":        ("IF-MIB", "ifNumber"),
    "1.3.6.1.2.1.2.2.1.1":    ("IF-MIB", "ifIndex"),
    "1.3.6.1.2.1.2.2.1.2":    ("IF-MIB", "ifDescr"),
    "1.3.6.1.2.1.2.2.1.3":    ("IF-MIB", "ifType"),
    "1.3.6.1.2.1.2.2.1.4":    ("IF-MIB", "ifMtu"),
    "1.3.6.1.2.1.2.2.1.5":    ("IF-MIB", "ifSpeed"),
    "1.3.6.1.2.1.2.2.1.6":    ("IF-MIB", "ifPhysAddress"),
    "1.3.6.1.2.1.2.2.1.7":    ("IF-MIB", "ifAdminStatus"),
    "1.3.6.1.2.1.2.2.1.8":    ("IF-MIB", "ifOperStatus"),
    "1.3.6.1.2.1.2.2.1.9":    ("IF-MIB", "ifLastChange"),
    "1.3.6.1.2.1.2.2.1.10":   ("IF-MIB", "ifInOctets"),
    "1.3.6.1.2.1.2.2.1.11":   ("IF-MIB", "ifInUcastPkts"),
    "1.3.6.1.2.1.2.2.1.13":   ("IF-MIB", "ifInDiscards"),
    "1.3.6.1.2.1.2.2.1.14":   ("IF-MIB", "ifInErrors"),
    "1.3.6.1.2.1.2.2.1.15":   ("IF-MIB", "ifInUnknownProtos"),
    "1.3.6.1.2.1.2.2.1.16":   ("IF-MIB", "ifOutOctets"),
    "1.3.6.1.2.1.2.2.1.17":   ("IF-MIB", "ifOutUcastPkts"),
    "1.3.6.1.2.1.2.2.1.19":   ("IF-MIB", "ifOutDiscards"),
    "1.3.6.1.2.1.2.2.1.20":   ("IF-MIB", "ifOutErrors"),
    # ifXTable (64-bit counters)
    "1.3.6.1.2.1.31.1.1.1.1":  ("IF-MIB", "ifName"),
    "1.3.6.1.2.1.31.1.1.1.6":  ("IF-MIB", "ifHCInOctets"),
    "1.3.6.1.2.1.31.1.1.1.10": ("IF-MIB", "ifHCOutOctets"),
    "1.3.6.1.2.1.31.1.1.1.15": ("IF-MIB", "ifHighSpeed"),
    "1.3.6.1.2.1.31.1.1.1.18": ("IF-MIB", "ifAlias"),

    # ── BGP4-MIB (RFC 1657) ──────────────────────────────────────────────────
    "1.3.6.1.2.1.15.1":       ("BGP4-MIB", "bgpVersion"),
    "1.3.6.1.2.1.15.2":       ("BGP4-MIB", "bgpLocalAs"),
    "1.3.6.1.2.1.15.3.1.1":   ("BGP4-MIB", "bgpPeerIdentifier"),
    "1.3.6.1.2.1.15.3.1.2":   ("BGP4-MIB", "bgpPeerState"),
    "1.3.6.1.2.1.15.3.1.3":   ("BGP4-MIB", "bgpPeerAdminStatus"),
    "1.3.6.1.2.1.15.3.1.4":   ("BGP4-MIB", "bgpPeerNegotiatedVersion"),
    "1.3.6.1.2.1.15.3.1.5":   ("BGP4-MIB", "bgpPeerLocalAddr"),
    "1.3.6.1.2.1.15.3.1.6":   ("BGP4-MIB", "bgpPeerLocalPort"),
    "1.3.6.1.2.1.15.3.1.7":   ("BGP4-MIB", "bgpPeerRemoteAddr"),
    "1.3.6.1.2.1.15.3.1.8":   ("BGP4-MIB", "bgpPeerRemotePort"),
    "1.3.6.1.2.1.15.3.1.9":   ("BGP4-MIB", "bgpPeerRemoteAs"),
    "1.3.6.1.2.1.15.3.1.10":  ("BGP4-MIB", "bgpPeerInUpdates"),
    "1.3.6.1.2.1.15.3.1.11":  ("BGP4-MIB", "bgpPeerOutUpdates"),
    "1.3.6.1.2.1.15.3.1.12":  ("BGP4-MIB", "bgpPeerInTotalMessages"),
    "1.3.6.1.2.1.15.3.1.13":  ("BGP4-MIB", "bgpPeerOutTotalMessages"),
    "1.3.6.1.2.1.15.3.1.14":  ("BGP4-MIB", "bgpPeerLastError"),
    "1.3.6.1.2.1.15.3.1.16":  ("BGP4-MIB", "bgpPeerFsmEstablishedTime"),
    "1.3.6.1.2.1.15.3.1.21":  ("BGP4-MIB", "bgpPeerLocalAs"),
    "1.3.6.1.2.1.15.4":       ("BGP4-MIB", "bgpIdentifier"),
    # BGP4 traps
    "1.3.6.1.2.1.15.7.1":     ("BGP4-MIB", "bgpEstablished"),
    "1.3.6.1.2.1.15.7.2":     ("BGP4-MIB", "bgpBackwardTransition"),

    # ── UPS-MIB (RFC 1628) ───────────────────────────────────────────────────
    "1.3.6.1.2.1.33.1.1.1":   ("UPS-MIB", "upsIdentManufacturer"),
    "1.3.6.1.2.1.33.1.1.2":   ("UPS-MIB", "upsIdentModel"),
    "1.3.6.1.2.1.33.1.1.3":   ("UPS-MIB", "upsIdentUPSSoftwareVersion"),
    "1.3.6.1.2.1.33.1.1.4":   ("UPS-MIB", "upsIdentAgentSoftwareVersion"),
    "1.3.6.1.2.1.33.1.1.5":   ("UPS-MIB", "upsIdentName"),
    # Battery
    "1.3.6.1.2.1.33.1.2.1":   ("UPS-MIB", "upsBatteryStatus"),
    "1.3.6.1.2.1.33.1.2.2":   ("UPS-MIB", "upsSecondsOnBattery"),
    "1.3.6.1.2.1.33.1.2.3":   ("UPS-MIB", "upsEstimatedMinutesRemaining"),
    "1.3.6.1.2.1.33.1.2.4":   ("UPS-MIB", "upsEstimatedChargeRemaining"),
    "1.3.6.1.2.1.33.1.2.5":   ("UPS-MIB", "upsBatteryVoltage"),
    "1.3.6.1.2.1.33.1.2.6":   ("UPS-MIB", "upsBatteryCurrent"),
    "1.3.6.1.2.1.33.1.2.7":   ("UPS-MIB", "upsBatteryTemperature"),
    # Input
    "1.3.6.1.2.1.33.1.3.2":   ("UPS-MIB", "upsInputNumLines"),
    "1.3.6.1.2.1.33.1.3.3.1.2": ("UPS-MIB", "upsInputFrequency"),
    "1.3.6.1.2.1.33.1.3.3.1.3": ("UPS-MIB", "upsInputVoltage"),
    "1.3.6.1.2.1.33.1.3.3.1.4": ("UPS-MIB", "upsInputCurrent"),
    "1.3.6.1.2.1.33.1.3.3.1.5": ("UPS-MIB", "upsInputTruePower"),
    # Output
    "1.3.6.1.2.1.33.1.4.1":   ("UPS-MIB", "upsOutputSource"),
    "1.3.6.1.2.1.33.1.4.2":   ("UPS-MIB", "upsOutputFrequency"),
    "1.3.6.1.2.1.33.1.4.3":   ("UPS-MIB", "upsOutputNumLines"),
    "1.3.6.1.2.1.33.1.4.4.1.2": ("UPS-MIB", "upsOutputVoltage"),
    "1.3.6.1.2.1.33.1.4.4.1.3": ("UPS-MIB", "upsOutputCurrent"),
    "1.3.6.1.2.1.33.1.4.4.1.4": ("UPS-MIB", "upsOutputPower"),
    "1.3.6.1.2.1.33.1.4.4.1.5": ("UPS-MIB", "upsOutputPercentLoad"),
    # Alarms
    "1.3.6.1.2.1.33.1.6.1":   ("UPS-MIB", "upsAlarmsPresent"),
    # Well-known alarm OIDs
    "1.3.6.1.2.1.33.1.9.1":   ("UPS-MIB", "upsWellKnownAlarmsOnBattery"),
    "1.3.6.1.2.1.33.1.9.2":   ("UPS-MIB", "upsWellKnownAlarmsLowBattery"),
    "1.3.6.1.2.1.33.1.9.3":   ("UPS-MIB", "upsWellKnownAlarmsDepletedBattery"),
    "1.3.6.1.2.1.33.1.9.4":   ("UPS-MIB", "upsWellKnownAlarmsTempBad"),
    "1.3.6.1.2.1.33.1.9.5":   ("UPS-MIB", "upsWellKnownAlarmsInputBad"),
    "1.3.6.1.2.1.33.1.9.6":   ("UPS-MIB", "upsWellKnownAlarmsOutputBad"),
    # Traps
    "1.3.6.1.2.1.33.2.0.1":   ("UPS-MIB", "upsTrapOnBattery"),
    "1.3.6.1.2.1.33.2.0.2":   ("UPS-MIB", "upsTrapLowBattery"),
    "1.3.6.1.2.1.33.2.0.3":   ("UPS-MIB", "upsTrapDepletedBattery"),
    "1.3.6.1.2.1.33.2.0.4":   ("UPS-MIB", "upsTrapTempBad"),
    "1.3.6.1.2.1.33.2.0.5":   ("UPS-MIB", "upsTrapInputBad"),
    "1.3.6.1.2.1.33.2.0.6":   ("UPS-MIB", "upsTrapOutputBad"),
    "1.3.6.1.2.1.33.2.0.7":   ("UPS-MIB", "upsTrapCommunicationsLost"),
    "1.3.6.1.2.1.33.2.0.8":   ("UPS-MIB", "upsTrapCommunicationsEstablished"),
    "1.3.6.1.2.1.33.2.0.9":   ("UPS-MIB", "upsTrapShutdownPending"),
    "1.3.6.1.2.1.33.2.0.10":  ("UPS-MIB", "upsTrapShutdownImminent"),
    "1.3.6.1.2.1.33.2.0.11":  ("UPS-MIB", "upsTrapTestCompleted"),

    # ── APC-POWERNET-MIB (enterprise 1.3.6.1.4.1.318) ────────────────────────
    # Battery
    "1.3.6.1.4.1.318.1.1.1.2.2.1":  ("APC-POWERNET-MIB", "upsAdvBatteryCapacity"),
    "1.3.6.1.4.1.318.1.1.1.2.2.2":  ("APC-POWERNET-MIB", "upsAdvBatteryTemperature"),
    "1.3.6.1.4.1.318.1.1.1.2.2.3":  ("APC-POWERNET-MIB", "upsAdvBatteryRunTimeRemaining"),
    "1.3.6.1.4.1.318.1.1.1.2.2.4":  ("APC-POWERNET-MIB", "upsAdvBatteryReplaceIndicator"),
    "1.3.6.1.4.1.318.1.1.1.2.2.6":  ("APC-POWERNET-MIB", "upsAdvBatteryActualVoltage"),
    "1.3.6.1.4.1.318.1.1.1.2.3.2":  ("APC-POWERNET-MIB", "upsAdvBatteryChargerStatus"),
    # Input
    "1.3.6.1.4.1.318.1.1.1.3.3.1":  ("APC-POWERNET-MIB", "upsAdvInputLineVoltage"),
    "1.3.6.1.4.1.318.1.1.1.3.3.2":  ("APC-POWERNET-MIB", "upsAdvInputMaxLineVoltage"),
    "1.3.6.1.4.1.318.1.1.1.3.3.3":  ("APC-POWERNET-MIB", "upsAdvInputMinLineVoltage"),
    "1.3.6.1.4.1.318.1.1.1.3.3.4":  ("APC-POWERNET-MIB", "upsAdvInputFrequency"),
    # Output
    "1.3.6.1.4.1.318.1.1.1.4.2.1":  ("APC-POWERNET-MIB", "upsAdvOutputVoltage"),
    "1.3.6.1.4.1.318.1.1.1.4.2.2":  ("APC-POWERNET-MIB", "upsAdvOutputFrequency"),
    "1.3.6.1.4.1.318.1.1.1.4.2.3":  ("APC-POWERNET-MIB", "upsAdvOutputLoad"),
    "1.3.6.1.4.1.318.1.1.1.4.2.4":  ("APC-POWERNET-MIB", "upsAdvOutputCurrent"),
    "1.3.6.1.4.1.318.1.1.1.4.2.8":  ("APC-POWERNET-MIB", "upsAdvOutputActivePower"),
    "1.3.6.1.4.1.318.1.1.1.4.2.9":  ("APC-POWERNET-MIB", "upsAdvOutputApparentPower"),
    "1.3.6.1.4.1.318.1.1.1.11.1.1": ("APC-POWERNET-MIB", "upsBasicStateOutputState"),
    # APC traps
    "1.3.6.1.4.1.318.0.3":   ("APC-POWERNET-MIB", "apcUpsOverload"),
    "1.3.6.1.4.1.318.0.5":   ("APC-POWERNET-MIB", "apcUpsLowBattery"),
    "1.3.6.1.4.1.318.0.6":   ("APC-POWERNET-MIB", "apcUpsReturnFromOnBattery"),
    "1.3.6.1.4.1.318.0.9":   ("APC-POWERNET-MIB", "apcUpsOnBattery"),
    "1.3.6.1.4.1.318.0.15":  ("APC-POWERNET-MIB", "apcUpsDischarged"),
    "1.3.6.1.4.1.318.0.16":  ("APC-POWERNET-MIB", "apcUpsCharged"),
}


def resolve(oid: str) -> tuple[str, str, str]:
    """
    Resolve an OID to (mib_name, object_name, instance).

    Examples:
      resolve("1.3.6.1.2.1.1.1.0")
        → ("SNMPv2-MIB", "sysDescr", "0")
      resolve("1.3.6.1.2.1.2.2.1.10.3")
        → ("IF-MIB", "ifInOctets", "3")
      resolve("1.2.3.4.5")
        → ("unknown", "1.2.3.4.5", "")
    """
    # 1. Exact match (handles scalar OIDs like "1.3.6.1.2.1.1.1.0" directly)
    if oid in _TABLE:
        mib, name = _TABLE[oid]
        return (mib, name, "")

    # 2. Walk progressively shorter prefixes to find table column + instance
    parts = oid.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:depth])
        if prefix in _TABLE:
            mib, name = _TABLE[prefix]
            instance = ".".join(parts[depth:])
            return (mib, name, instance)

    return ("unknown", oid, "")


def display_name(oid: str) -> str:
    """Return a compact human-readable label like 'IF-MIB::ifInOctets.3'."""
    mib, name, instance = resolve(oid)
    if mib == "unknown":
        return oid
    label = f"{mib}::{name}"
    if instance:
        label = f"{label}.{instance}"
    return label
