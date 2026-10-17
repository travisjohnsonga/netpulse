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

    def test_fortios_device_oids(self):
        # FortiOS gets the Fortinet enterprise CPU/memory OIDs.
        from apps.devices.snmp_publish import _device_oids
        oids = _device_oids("fortios")
        assert "1.3.6.1.4.1.12356.101.4.1.3.0" in oids   # fgSysCpuUsage
        assert "1.3.6.1.4.1.12356.101.4.1.4.0" in oids   # fgSysMemUsage
        assert "1.3.6.1.4.1.12356.101.4.1.5.0" in oids   # fgSysMemCapacity
        assert "1.3.6.1.2.1.1.3.0" in oids               # sysUpTime

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

    def test_aos_cx_gets_memory_get_oids_and_walk_bases(self):
        from apps.devices.snmp_publish import (
            build_device_payload, SYSUPTIME, AOSCX_MEM_SIZE, AOSCX_MEM_USED, HRPROCLOAD,
        )
        from apps.telemetry.snmp_environment import HR_PROCESSOR_LOAD, ENT_SENSOR_VALUE
        payload = build_device_payload(self._dev("aos_cx"))
        oids = payload["poll_oids"]
        # Memory comes from hrStorage GET; CPU is NOT a fixed GET (.1 is empty).
        assert SYSUPTIME in oids and AOSCX_MEM_SIZE in oids and AOSCX_MEM_USED in oids
        assert HRPROCLOAD not in oids
        # CPU + sensor tables are walked instead.
        assert HR_PROCESSOR_LOAD in payload["walk_oids"]
        assert ENT_SENSOR_VALUE in payload["walk_oids"]

    def test_non_aos_cx_has_no_walk_oids(self):
        from apps.devices.snmp_publish import build_device_payload
        assert build_device_payload(self._dev("ios_xe"))["walk_oids"] == []


class TestPollNow:
    def test_poll_now_triggers_publish(self, auth_client, snmp_v3_device, monkeypatch):
        from apps.devices import snmp_publish
        captured = {}
        monkeypatch.setattr(snmp_publish, "_run", lambda msgs: captured.setdefault("msgs", msgs) or True)
        resp = auth_client.post(f"/api/devices/{snmp_v3_device.id}/poll-now/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "poll triggered"
        assert captured["msgs"][0][0] == snmp_publish.UPSERT_SUBJECT
        # The poller ignores unchanged configs; poll_now forces a re-poll.
        assert captured["msgs"][0][1]["poll_now"] is True

    def test_poll_now_not_pollable(self, auth_client):
        from apps.devices.models import Device
        d = Device.objects.create(hostname="nosnmp2", ip_address="10.0.0.77", status="active")
        resp = auth_client.post(f"/api/devices/{d.id}/poll-now/")
        assert resp.status_code == 400
        assert resp.json()["status"] == "not pollable"

    def test_poll_now_requires_auth(self, api_client, snmp_v3_device):
        assert api_client.post(f"/api/devices/{snmp_v3_device.id}/poll-now/").status_code == 401


class TestPollerSync:
    """Config changes that bypass post_save still reach the poller, and the
    scheduler's periodic full resync covers a restarted (empty) poller."""

    @pytest.fixture
    def published(self, monkeypatch):
        from apps.devices import snmp_publish
        calls = []
        monkeypatch.setattr(snmp_publish, "publish_device_upsert",
                            lambda device: calls.append(
                                (device.id, sorted(device.monitored_interfaces
                                                   .values_list("if_name", flat=True)))))
        monkeypatch.setattr(snmp_publish, "publish_device_remove",
                            lambda device_id: calls.append(("removed", device_id)))
        return calls

    def _iface(self, name, idx):
        return {"if_name": name, "if_index": idx}

    def test_interface_selection_republishes_once_with_final_state(
            self, auth_client, snmp_v3_device, published, django_capture_on_commit_callbacks):
        # Replacing the selection = bulk delete (N post_delete signals) +
        # bulk_create (no post_save): exactly one publish, with the new set.
        url = f"/api/devices/{snmp_v3_device.id}/interfaces/"
        with django_capture_on_commit_callbacks(execute=True):
            resp = auth_client.post(url, {"interfaces": [self._iface("Gi2", 2), self._iface("Gi3", 3)]},
                                    format="json")
        assert resp.status_code == 201
        assert published == [(snmp_v3_device.id, ["Gi2", "Gi3"])]

    def test_interface_delete_republishes(self, auth_client, snmp_v3_device, published,
                                          django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            resp = auth_client.delete(f"/api/devices/{snmp_v3_device.id}/interfaces/GigabitEthernet1/")
        assert resp.status_code == 204
        assert published == [(snmp_v3_device.id, [])]

    def test_alert_config_update_republishes(self, auth_client, snmp_v3_device, published,
                                             django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            resp = auth_client.post(f"/api/devices/{snmp_v3_device.id}/interfaces/alert-config/",
                                    {"if_names": ["GigabitEthernet1"], "alert_on_down": False},
                                    format="json")
        assert resp.status_code == 200
        assert published == [(snmp_v3_device.id, ["GigabitEthernet1"])]

    def test_device_delete_does_not_re_add_it(self, snmp_v3_device, published,
                                              django_capture_on_commit_callbacks):
        # Cascade-deleting the device's interfaces must not republish the
        # device after its removal message.
        dev_id = snmp_v3_device.id
        with django_capture_on_commit_callbacks(execute=True):
            snmp_v3_device.delete()
        assert published == [("removed", dev_id)]

    def test_scheduler_sync_publishes_all(self, snmp_v3_device, monkeypatch, settings):
        from apps.core.management.commands.run_scheduler import Command
        from apps.devices import snmp_publish
        calls = []
        monkeypatch.setattr(snmp_publish, "publish_all_active", lambda: calls.append(1) or 1)
        settings.SNMP_DEVICE_PUBLISH = True
        Command()._sync_snmp_configs()
        settings.SNMP_DEVICE_PUBLISH = False
        Command()._sync_snmp_configs()
        assert calls == [1]
