"""
Build a pysnmp auth object from a credential profile + OpenBao secrets.

Shared by device enrichment (apps.devices.enrich) and interface discovery
(apps.telemetry.discovery) so both speak SNMP the same way: SNMPv3 (USM) when
the profile is v3, SNMPv2c (community) otherwise.
"""
from __future__ import annotations


def build_snmp_auth(profile, secrets):
    """pysnmp auth object from the device's credential profile + OpenBao secrets."""
    from pysnmp.hlapi.v3arch.asyncio import CommunityData, UsmUserData

    if profile.snmpv3_enabled and profile.snmpv3_username:
        from pysnmp.hlapi.v3arch.asyncio import (
            usmAesCfb128Protocol, usmAesCfb192Protocol, usmAesCfb256Protocol,
            usmDESPrivProtocol, usmHMAC128SHA224AuthProtocol, usmHMAC192SHA256AuthProtocol,
            usmHMAC256SHA384AuthProtocol, usmHMAC384SHA512AuthProtocol,
            usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
        )
        auth_map = {
            "MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol,
            "SHA224": usmHMAC128SHA224AuthProtocol, "SHA256": usmHMAC192SHA256AuthProtocol,
            "SHA384": usmHMAC256SHA384AuthProtocol, "SHA512": usmHMAC384SHA512AuthProtocol,
        }
        priv_map = {
            "DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol,
            "AES128": usmAesCfb128Protocol, "AES192": usmAesCfb192Protocol,
            "AES256": usmAesCfb256Protocol,
        }
        auth_p = auth_map.get((profile.snmpv3_auth_protocol or "SHA").upper(), usmHMACSHAAuthProtocol)
        priv_p = priv_map.get((profile.snmpv3_priv_protocol or "AES").upper(), usmAesCfb128Protocol)
        level = profile.snmpv3_security_level or "authPriv"
        auth_key = secrets.get("snmpv3_auth_key") or None
        priv_key = secrets.get("snmpv3_priv_key") or None
        if level == "noAuthNoPriv":
            return UsmUserData(profile.snmpv3_username)
        if level == "authNoPriv" or not priv_key:
            return UsmUserData(profile.snmpv3_username, auth_key, authProtocol=auth_p)
        return UsmUserData(profile.snmpv3_username, auth_key, priv_key,
                           authProtocol=auth_p, privProtocol=priv_p)
    # SNMPv2c (community).
    return CommunityData(secrets.get("snmpv2c_community") or "public", mpModel=1)
