"""
Data models for SNMP device configuration.

Credentials are NEVER stored here.  The `cred_path` field is a reference to a
secret in OpenBao KV.  The CredentialManager resolves it at poll time.

OpenBao secret format at `secret/<cred_path>`:
  SNMPv1 / v2c:
    {"community": "public"}

  SNMPv3:
    {"security_name": "user1",
     "auth_protocol": "SHA256",   # MD5 | SHA | SHA224 | SHA256 | SHA384 | SHA512
     "auth_key":      "authpass",
     "priv_protocol": "AES",      # DES | AES | AES192 | AES256  (omit for noPriv)
     "priv_key":      "privpass"}  # omit for authNoPriv / noAuthNoPriv
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PollProfile:
    """A set of OIDs polled together at a given interval."""

    name: str
    oids: list[str]
    interval_seconds: int = 60

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PollProfile":
        return cls(
            name=d["name"],
            oids=d["oids"],
            interval_seconds=int(d.get("interval_seconds", 60)),
        )


@dataclass
class Device:
    """A single managed device.  Credentials live in OpenBao, not here."""

    device_id: str
    hostname: str
    ip: str
    port: int = 161
    version: int = 2            # 1 = SNMPv1, 2 = SNMPv2c, 3 = SNMPv3
    cred_path: str = ""         # OpenBao KV path, e.g. "snmp/router1"
    poll_profiles: list[PollProfile] = field(default_factory=list)

    # ── Derived / convenience ──────────────────────────────────────────────

    @property
    def label(self) -> str:
        return self.hostname or self.ip

    # ── Serialisation ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Device":
        profiles = [PollProfile.from_dict(p) for p in d.get("poll_profiles", [])]
        # Shorthand: top-level poll_oids / poll_interval → single "default" profile
        if not profiles and d.get("poll_oids"):
            profiles = [
                PollProfile(
                    name="default",
                    oids=d["poll_oids"],
                    interval_seconds=int(d.get("poll_interval", 60)),
                )
            ]
        return cls(
            device_id=d["device_id"],
            hostname=d.get("hostname", d.get("ip", "")),
            ip=d["ip"],
            port=int(d.get("port", 161)),
            version=int(d.get("version", 2)),
            cred_path=d.get("cred_path", ""),
            poll_profiles=profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip": self.ip,
            "port": self.port,
            "version": self.version,
            "cred_path": self.cred_path,
            "poll_profiles": [
                {"name": p.name, "oids": p.oids, "interval_seconds": p.interval_seconds}
                for p in self.poll_profiles
            ],
        }


# ── SNMP authentication helpers ───────────────────────────────────────────────

def build_community_data(version: int, creds: dict[str, Any]):
    """Return a pysnmp CommunityData object for v1 or v2c."""
    from pysnmp.hlapi.asyncio import CommunityData
    community = creds.get("community", "public")
    return CommunityData(community, mpModel=version - 1)   # mpModel: 0=v1, 1=v2c


def build_usm_data(creds: dict[str, Any]):
    """Return a pysnmp UsmUserData object for v3."""
    from pysnmp.hlapi.asyncio import (
        UsmUserData,
        usmHMACMD5AuthProtocol,
        usmHMACSHAAuthProtocol,
        usmAesCfb128Protocol,
        usmDESPrivProtocol,
        usmNoAuthProtocol,
        usmNoPrivProtocol,
    )

    try:
        from pysnmp.hlapi.asyncio import usmHMAC192SHA256AuthProtocol
    except ImportError:
        usmHMAC192SHA256AuthProtocol = usmHMACSHAAuthProtocol
    try:
        from pysnmp.hlapi.asyncio import usmAesCfb256Protocol
    except ImportError:
        usmAesCfb256Protocol = usmAesCfb128Protocol

    _AUTH = {
        "MD5":    usmHMACMD5AuthProtocol,
        "SHA":    usmHMACSHAAuthProtocol,
        "SHA256": usmHMAC192SHA256AuthProtocol,
        None:     usmNoAuthProtocol,
    }
    _PRIV = {
        "DES":    usmDESPrivProtocol,
        "AES":    usmAesCfb128Protocol,
        "AES256": usmAesCfb256Protocol,
        None:     usmNoPrivProtocol,
    }

    name = creds["security_name"]
    auth_key = creds.get("auth_key")
    priv_key = creds.get("priv_key")
    auth_proto = _AUTH.get(creds.get("auth_protocol"), usmNoAuthProtocol)
    priv_proto = _PRIV.get(creds.get("priv_protocol"), usmNoPrivProtocol)

    if auth_key and priv_key:
        return UsmUserData(name, authKey=auth_key, privKey=priv_key,
                           authProtocol=auth_proto, privProtocol=priv_proto)
    if auth_key:
        return UsmUserData(name, authKey=auth_key, authProtocol=auth_proto)
    return UsmUserData(name)
