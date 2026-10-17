"""pysnmp 7 compatibility: SNMPv3 credential mapping, privacy crypto backend,
and trap decoding against real BER-encoded v1/v2c traps."""
import pytest
from pyasn1.codec.ber import encoder
from pysnmp.proto import api

from ingest.models import build_community_data, build_usm_data
from ingest.trap_receiver import decode_trap


# ── SNMPv3 USM mapping ────────────────────────────────────────────────────────

def _usm(auth="SHA", priv="AES", **kw):
    creds = {"security_name": "u", "auth_key": "authpass123", "priv_key": "privpass123",
             "auth_protocol": auth, "priv_protocol": priv}
    creds.update(kw)
    return build_usm_data(creds)


@pytest.mark.parametrize("auth,priv,auth_attr,priv_attr", [
    ("SHA", "AES", "usmHMACSHAAuthProtocol", "usmAesCfb128Protocol"),
    ("sha", "aes", "usmHMACSHAAuthProtocol", "usmAesCfb128Protocol"),       # case-insensitive
    ("MD5", "DES", "usmHMACMD5AuthProtocol", "usmDESPrivProtocol"),
    ("SHA224", "AES128", "usmHMAC128SHA224AuthProtocol", "usmAesCfb128Protocol"),
    ("SHA256", "AES192", "usmHMAC192SHA256AuthProtocol", "usmAesCfb192Protocol"),
    ("SHA-384", "AES256", "usmHMAC256SHA384AuthProtocol", "usmAesCfb256Protocol"),
    ("SHA512", "AES256", "usmHMAC384SHA512AuthProtocol", "usmAesCfb256Protocol"),
])
def test_usm_protocols(auth, priv, auth_attr, priv_attr):
    import pysnmp.hlapi.v3arch.asyncio as h
    u = _usm(auth, priv)
    assert u.authentication_protocol == getattr(h, auth_attr)
    assert u.privacy_protocol == getattr(h, priv_attr)


@pytest.mark.parametrize("auth,priv", [("SHA3", "AES"), ("SHA", "BLOWFISH")])
def test_unknown_protocol_is_an_error_not_noauth(auth, priv):
    with pytest.raises(ValueError, match="unsupported SNMPv3"):
        _usm(auth, priv)


def test_auth_only_and_no_auth():
    import pysnmp.hlapi.v3arch.asyncio as h
    u = build_usm_data({"security_name": "u", "auth_key": "authpass123", "auth_protocol": "SHA"})
    assert u.privacy_protocol == h.usmNoPrivProtocol
    assert build_usm_data({"security_name": "u"}).authentication_protocol == h.usmNoAuthProtocol


def test_community_data():
    cd = build_community_data(2, {"snmpv2c_community": "ro"})
    assert cd.message_processing_model == 1   # v2c


@pytest.mark.parametrize("module,cls", [("pysnmp.proto.secmod.rfc3826.priv.aes", "Aes"),
                                        ("pysnmp.proto.secmod.rfc3414.priv.des", "Des")])
def test_privacy_crypto_backend_available(module, cls):
    # pysnmp 7 needs the optional `cryptography` package for AES/DES; without
    # it every SNMPv3 authPriv poll fails at runtime. Round-trip a block.
    import importlib

    from pysnmp.proto.secmod.rfc3414 import localkey

    priv = getattr(importlib.import_module(module), cls)()
    from pyasn1.type import univ
    engine_id = univ.OctetString(b"\x80\x00\x1f\x88\x80" + b"\x01" * 8)
    key = localkey.localize_key_sha(localkey.hash_passphrase_sha(b"privpass123"), engine_id)
    plaintext = b"0123456789abcdef"
    ciphertext, salt = priv.encrypt_data(key, (1, 2, b""), plaintext)   # (boots, time, salt)
    assert bytes(ciphertext) != plaintext
    assert bytes(priv.decrypt_data(key, (1, 2, salt), ciphertext))[:len(plaintext)] == plaintext


# ── trap decoding ─────────────────────────────────────────────────────────────

def _v2c_trap() -> bytes:
    v2 = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    pdu = v2.SNMPv2TrapPDU()
    v2.apiTrapPDU.set_defaults(pdu)
    v2.apiTrapPDU.set_varbinds(pdu, [
        (v2.ObjectIdentifier("1.3.6.1.2.1.1.3.0"), v2.TimeTicks(12345)),
        (v2.ObjectIdentifier("1.3.6.1.6.3.1.1.4.1.0"), v2.ObjectIdentifier("1.3.6.1.6.3.1.1.5.3")),
        (v2.ObjectIdentifier("1.3.6.1.2.1.2.2.1.1.7"), v2.Integer(7)),
    ])
    msg = v2.Message()
    v2.apiMessage.set_defaults(msg)
    v2.apiMessage.set_community(msg, "public")
    v2.apiMessage.set_pdu(msg, pdu)
    return encoder.encode(msg)


def _v1_trap() -> bytes:
    v1 = api.PROTOCOL_MODULES[api.SNMP_VERSION_1]
    pdu = v1.TrapPDU()
    v1.apiTrapPDU.set_defaults(pdu)
    v1.apiTrapPDU.set_enterprise(pdu, (1, 3, 6, 1, 4, 1, 9))
    v1.apiTrapPDU.set_agent_address(pdu, "192.0.2.9")
    v1.apiTrapPDU.set_generic_trap(pdu, 2)          # linkDown
    v1.apiTrapPDU.set_specific_trap(pdu, 0)
    v1.apiTrapPDU.set_timestamp(pdu, 4242)
    msg = v1.Message()
    v1.apiMessage.set_defaults(msg)
    v1.apiMessage.set_community(msg, "public")
    v1.apiMessage.set_pdu(msg, pdu)
    return encoder.encode(msg)


def test_decode_v2c_trap():
    t = decode_trap(_v2c_trap(), "192.0.2.9", 50000)
    assert t["version"] == "v2c" and t["community"] == "public"
    assert t["trap_oid"] == "1.3.6.1.6.3.1.1.5.3"           # linkDown
    assert t["uptime_hundredths"] == "12345"
    assert any(v["oid"] == "1.3.6.1.2.1.2.2.1.1.7" for v in t["varbinds"])


def test_decode_v1_trap():
    t = decode_trap(_v1_trap(), "192.0.2.10", 50000)
    assert t["version"] == "v1" and t["community"] == "public"
    assert t["agent_addr"] == "192.0.2.9"                    # dotted quad, not raw octets
    assert t["generic_trap"] == 2 and t["uptime_hundredths"] == 4242
    assert t["trap_oid"]                                        # generic → standard OID


def test_decode_garbage_is_none():
    assert decode_trap(b"\x00\x01garbage", "192.0.2.9", 1) is None
