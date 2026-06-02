"""Integration: enrichment — collection-status shape + platform detection."""
import pytest

from apps.devices.management.commands.run_discovery import _platform_from_descr
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(hostname="enrich-rtr", ip_address="10.6.0.1",
                                 status="active")


class TestCollectionStatusEndpoint:
    def test_shape(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.id}/collection-status/")
        assert resp.status_code == 200
        body = resp.json()
        # Documented top-level keys.
        for key in ("gnmi", "snmp", "primary", "any_active"):
            assert key in body, f"missing {key}"
        assert "active" in body["gnmi"]
        assert "active" in body["snmp"]
        # `active` flags are booleans; `any_active` reflects them. We don't
        # assert a specific value: the api container shares Valkey/InfluxDB with
        # the live platform, so a low device id can coincide with a streaming
        # device's heartbeat. Shape + type is what we verify here.
        assert isinstance(body["gnmi"]["active"], bool)
        assert isinstance(body["snmp"]["active"], bool)
        assert isinstance(body["any_active"], bool)

    def test_requires_auth(self, api_client, device):
        assert api_client.get(
            f"/api/devices/{device.id}/collection-status/"
        ).status_code == 401


class TestPlatformDetection:
    @pytest.mark.parametrize(
        "descr,expected",
        [
            ("Cisco IOS Software, IOS-XE Software, Catalyst L3", "ios_xe"),
            ("Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M)", "ios"),
            ("FortiGate-60F v7.2.5 build1517 (GA)", "fortios"),
            ("Cisco NX-OS(tm) n9000", "nxos"),
            ("Juniper Networks, Inc. junos 21.4R3", "junos"),
            ("Arista Networks EOS version 4.30", "eos"),
            ("Some unknown vendor box", ""),
        ],
    )
    def test_platform_from_descr(self, descr, expected):
        assert _platform_from_descr(descr) == expected
