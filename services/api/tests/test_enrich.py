"""Post-approval device enrichment (apps.devices.enrich)."""
import pytest

from apps.credentials.models import CredentialProfile
from apps.devices import enrich
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile():
    return CredentialProfile.objects.create(name="p", snmpv3_enabled=True, snmpv3_username="u")


@pytest.fixture
def device(profile):
    return Device.objects.create(
        hostname="rtr", ip_address="10.0.0.1", management_ip="10.0.0.1",
        platform="ios", credential_profile=profile)


def _no_network(monkeypatch):
    """Stub all probe/IO so enrich_device runs offline."""
    monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: {})
    monkeypatch.setattr(enrich, "_ssh_collect", lambda ip, p, s: {})
    monkeypatch.setattr(enrich, "_discover_interfaces", lambda d: ([], 0, 0))
    monkeypatch.setattr(enrich, "_discover_lldp", lambda d, i=None: 0)
    monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: None)
    monkeypatch.setattr(enrich, "_collect_config", lambda d: None)


class TestEnrichDevice:
    def test_snmp_populates_and_corrects_platform(self, device, monkeypatch):
        _no_network(monkeypatch)
        snmp = {
            enrich._OID_SYS_DESCR: "Cisco IOS-XE Software, C8000V Software, Version 17.12.4",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.9.1.2862",
            enrich._OID_ENT_MODEL: "C8000V",
            enrich._OID_ENT_SERIAL: "ABCD1234",
        }
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: snmp)
        changed = enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.platform == "ios_xe"      # corrected from 'ios'
        assert device.os_version == "17.12.4"
        assert device.model == "C8000V"
        assert device.serial_number == "ABCD1234"
        assert device.vendor == "cisco"
        assert set(changed) >= {"platform", "os_version", "model", "serial_number"}

    def test_model_from_sysobjid_when_descr_has_none(self, device, monkeypatch):
        _no_network(monkeypatch)
        snmp = {
            enrich._OID_SYS_DESCR: "Some router OS",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.9.1.1745",  # → CSR1000V
        }
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: snmp)
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.model == "CSR1000V"

    def test_ssh_fallback_fills_gaps(self, device, monkeypatch):
        _no_network(monkeypatch)
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: {})  # SNMP silent
        monkeypatch.setattr(enrich, "_ssh_collect", lambda ip, p, s: {
            "detected": True, "platform": "ios_xe", "os_version": "17.12",
            "model": "C8000V", "serial": "XYZ9", "vendor": "cisco"})
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.os_version == "17.12"
        assert device.model == "C8000V"
        assert device.serial_number == "XYZ9"

    def test_misclassified_other_runs_rest_in_one_pass(self, device, monkeypatch):
        # Device was added as "other" (SSH-detect bug). SNMP reveals AOS-CX, so a
        # single re-run should also fire the preferred REST collector for detail.
        device.platform = "other"
        device.save()
        _no_network(monkeypatch)
        snmp = {enrich._OID_SYS_DESCR: "ArubaOS-CX 10.10.1010, Aruba6300M"}
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: snmp)
        rest_calls = []
        def fake_rest(ip, p, s):
            rest_calls.append(ip)
            return {"hostname": "core-sw-1", "version": "FL.10.10.1010",
                    "model": "6300M", "serial": "SG12345"}
        monkeypatch.setattr(enrich, "_aos_cx_collect", fake_rest)
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.platform == "aos_cx" and device.vendor == "aruba"
        assert device.model == "6300M" and device.serial_number == "SG12345"
        assert rest_calls, "REST collector should run once SNMP reveals AOS-CX"

    def test_does_not_blank_existing_fields(self, device, monkeypatch):
        device.model = "KEEP-ME"
        device.os_version = "1.0"
        device.save()
        _no_network(monkeypatch)  # everything returns empty
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.model == "KEEP-ME"
        assert device.os_version == "1.0"

    def test_no_credential_profile_is_noop(self, monkeypatch):
        _no_network(monkeypatch)
        dev = Device.objects.create(hostname="bare", ip_address="10.0.0.2")
        assert enrich.enrich_device(dev.id) == {}

    def test_steps_are_independent(self, device, monkeypatch):
        # SNMP raises inside its collector path → device-info step still records
        # nothing, but interface + LLDP steps still run.
        calls = {"iface": 0, "lldp": 0}
        monkeypatch.setattr(enrich, "_snmp_collect", lambda *a: {})
        monkeypatch.setattr(enrich, "_ssh_collect", lambda *a: {})
        monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: None)
        monkeypatch.setattr(enrich, "_collect_config", lambda d: None)

        def boom_iface(d):
            calls["iface"] += 1
            raise RuntimeError("iface down")
        def lldp(d, i=None):
            calls["lldp"] += 1
            return 0
        monkeypatch.setattr(enrich, "_discover_interfaces", boom_iface)
        monkeypatch.setattr(enrich, "_discover_lldp", lldp)
        enrich.enrich_device(device.id)   # must not raise
        assert calls["iface"] == 1 and calls["lldp"] == 1


class TestEnrichChain:
    def test_discovers_interfaces_and_lldp(self, device, monkeypatch):
        from apps.devices import topology
        from apps.telemetry import discovery
        from apps.telemetry.models import MonitoredInterface

        ifaces = [
            {"if_name": "GigabitEthernet1", "if_index": 1, "oper_status": "up",
             "lldp_neighbor_hostname": "router2", "lldp_neighbor_port": "Gi1",
             "auto_select": True, "collection_method": "snmp"},
            {"if_name": "Loopback0", "auto_select": False},
        ]
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: ifaces)
        monkeypatch.setattr(topology, "discover_links",
                            lambda d, interfaces=None: [{"matched_device_id": 99}])
        monkeypatch.setattr(enrich, "_snmp_collect", lambda *a: {})
        monkeypatch.setattr(enrich, "_ssh_collect", lambda *a: {})
        published = []
        monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: published.append(did))
        collected = []
        monkeypatch.setattr(enrich, "_collect_config", lambda d: collected.append(d.id))

        enrich.enrich_device(device.id)
        # Only the LLDP-connected (auto-selected) interface is monitored.
        rows = MonitoredInterface.objects.filter(device=device)
        assert rows.count() == 1
        assert rows.first().if_name == "GigabitEthernet1"
        # Topology refresh published because a link matched.
        assert published == [device.id]
        # Step 4: initial config collection ran for the device.
        assert collected == [device.id]


class TestTriggerEnrich:
    def test_disabled_by_setting(self, device, settings, monkeypatch):
        settings.DEVICE_AUTO_ENRICH = False
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        assert enrich.trigger_enrich(device) is False
        assert scheduled == []

    def test_no_profile_not_scheduled(self, settings, monkeypatch):
        settings.DEVICE_AUTO_ENRICH = True
        dev = Device.objects.create(hostname="np", ip_address="10.0.0.3")
        # Patch on_commit only after creating the device (its save() signal also
        # schedules an on_commit we don't want to capture).
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        assert enrich.trigger_enrich(dev) is False
        assert scheduled == []

    def test_schedules_when_enabled(self, device, settings, monkeypatch):
        settings.DEVICE_AUTO_ENRICH = True
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        assert enrich.trigger_enrich(device) is True
        assert len(scheduled) == 1


class TestEnrichEndpoint:
    def test_requires_auth(self, api_client, device):
        assert api_client.post(f"/api/devices/{device.id}/enrich/").status_code == 401

    def test_returns_202(self, auth_client, device):
        resp = auth_client.post(f"/api/devices/{device.id}/enrich/")
        assert resp.status_code == 202
        assert resp.json()["device_id"] == device.id
