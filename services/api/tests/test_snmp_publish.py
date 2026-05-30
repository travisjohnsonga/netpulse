import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def snmp_v3_device():
    from apps.credentials.models import CredentialProfile
    from apps.devices.models import Device
    from apps.telemetry.models import MonitoredInterface, TelemetryConfig
    p = CredentialProfile.objects.create(
        name="DC SNMPv3", snmpv3_enabled=True, snmpv3_username="testsnmp",
        snmpv3_auth_protocol="SHA", snmpv3_priv_protocol="AES",
        snmpv3_security_level="authPriv", snmpv3_port=161,
        vault_path="netpulse/credentials/1",
    )
    d = Device.objects.create(hostname="router1", ip_address="192.168.98.100",
                              platform="ios_xe", status="active", credential_profile=p)
    TelemetryConfig.objects.create(device=d, snmp_interval=120)
    MonitoredInterface.objects.create(device=d, if_name="GigabitEthernet1", if_index=1,
                                      poll_traffic=True, poll_errors=True, poll_status=True)
    return d


class TestBuildDevicePayload:
    def test_includes_non_secret_snmp_fields(self, snmp_v3_device):
        from apps.devices.snmp_publish import build_device_payload
        p = build_device_payload(snmp_v3_device)
        assert p["device_id"] == str(snmp_v3_device.id)
        assert p["hostname"] == "router1"
        assert p["ip"] == "192.168.98.100"
        assert p["version"] == 3
        assert p["cred_path"] == "netpulse/credentials/1"
        assert p["snmp_username"] == "testsnmp"
        assert p["snmp_auth_protocol"] == "SHA"
        assert p["snmp_priv_protocol"] == "AES"
        assert p["snmp_security_level"] == "authPriv"
        assert p["poll_interval"] == 120

    def test_never_includes_secrets(self, snmp_v3_device):
        from apps.devices.snmp_publish import build_device_payload
        import json
        blob = json.dumps(build_device_payload(snmp_v3_device)).lower()
        for forbidden in ("auth_key", "priv_key", "community", "password", "secret"):
            assert forbidden not in blob

    def test_interfaces_and_oids(self, snmp_v3_device):
        from apps.devices.snmp_publish import build_device_payload
        p = build_device_payload(snmp_v3_device)
        assert p["interfaces"][0]["if_name"] == "GigabitEthernet1"
        assert p["interfaces"][0]["if_index"] == 1
        # device sysUpTime + per-interface ifHCInOctets.1 present
        assert "1.3.6.1.2.1.1.3.0" in p["poll_oids"]
        assert "1.3.6.1.2.1.31.1.1.1.6.1" in p["poll_oids"]

    def test_inactive_device_not_published(self, snmp_v3_device):
        from apps.devices.snmp_publish import build_device_payload
        snmp_v3_device.status = "inactive"; snmp_v3_device.save()
        assert build_device_payload(snmp_v3_device) is None

    def test_device_without_snmp_profile_not_published(self):
        from apps.devices.models import Device
        from apps.devices.snmp_publish import build_device_payload
        d = Device.objects.create(hostname="nosnmp", ip_address="10.0.0.9", status="active")
        assert build_device_payload(d) is None

    def test_publish_all_active_publishes_pollable_devices(self, snmp_v3_device, monkeypatch):
        # Stub the NATS layer so the test is hermetic.
        from apps.devices import snmp_publish
        captured = {}
        monkeypatch.setattr(snmp_publish, "_run", lambda msgs: captured.setdefault("msgs", msgs) or True)
        n = snmp_publish.publish_all_active()
        assert n == 1
        subject, payload = captured["msgs"][0]
        assert subject == snmp_publish.UPSERT_SUBJECT
        assert payload["hostname"] == "router1"

    def test_publish_all_active_returns_zero_on_nats_failure(self, snmp_v3_device, monkeypatch):
        from apps.devices import snmp_publish
        monkeypatch.setattr(snmp_publish, "_run", lambda msgs: False)
        assert snmp_publish.publish_all_active() == 0


class TestPlatformOIDs:
    def _dev(self, platform):
        from apps.credentials.models import CredentialProfile
        from apps.devices.models import Device
        p = CredentialProfile.objects.create(name=f"p-{platform}", snmpv2c_enabled=True, vault_path="x")
        return Device.objects.create(hostname=f"h-{platform}", ip_address="10.0.0.1",
                                     platform=platform, status="active", credential_profile=p)

    def test_cisco_gets_hrproc_and_cisco_mem(self):
        from apps.devices.snmp_publish import build_device_payload, HRPROCLOAD, CISCO_MEM_USED, SYSUPTIME
        oids = build_device_payload(self._dev("ios_xe"))["poll_oids"]
        assert SYSUPTIME in oids and HRPROCLOAD in oids and CISCO_MEM_USED in oids

    def test_junos_gets_juniper_cpu_not_cisco_mem(self):
        from apps.devices.snmp_publish import build_device_payload, HRPROCLOAD, CISCO_MEM_USED
        oids = build_device_payload(self._dev("junos"))["poll_oids"]
        assert HRPROCLOAD in oids
        assert "1.3.6.1.4.1.2636.3.1.13.1.8.9.1.0.0" in oids
        assert CISCO_MEM_USED not in oids

    def test_unknown_platform_uses_default(self):
        from apps.devices.snmp_publish import build_device_payload, SYSUPTIME, HRPROCLOAD
        oids = build_device_payload(self._dev("fortios"))["poll_oids"]
        # _default = just sysUpTime + hrProcessorLoad (+ any interface OIDs)
        assert SYSUPTIME in oids and HRPROCLOAD in oids
        assert "1.3.6.1.4.1.9.9.48.1.1.1.5.1" not in oids
