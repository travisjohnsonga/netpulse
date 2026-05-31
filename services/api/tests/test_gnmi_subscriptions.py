"""
Tests for multi-vendor push-telemetry config generation.
"""
import pytest

from apps.devices.models import Device
from apps.telemetry.models import MonitoredInterface
from apps.telemetry import config_gen, gnmi_subscriptions as gs

pytestmark = pytest.mark.django_db


def _device(platform="ios_xe", **kw):
    return Device.objects.create(
        hostname=kw.pop("hostname", "router1"),
        ip_address=kw.pop("ip_address", "10.0.0.1"),
        platform=platform,
        status="active",
        **kw,
    )


def _ifaces(device, names):
    for i, n in enumerate(names, start=1):
        MonitoredInterface.objects.create(device=device, if_index=i, if_name=n)
    return list(device.monitored_interfaces.all().order_by("if_index"))


class TestIosXe:
    def test_per_interface_subscriptions(self):
        d = _device()
        ifaces = _ifaces(d, ["GigabitEthernet1", "GigabitEthernet2"])
        cfg = gs.generate_iosxe_gnmi(d, "192.168.98.134", ifaces)
        # One subscription per interface, starting at 200, targeted xpath.
        assert "telemetry ietf subscription 200" in cfg
        assert "telemetry ietf subscription 201" in cfg
        assert "interface[name='GigabitEthernet1']" in cfg
        assert "interface[name='GigabitEthernet2']" in cfg
        # Device-level health subs present (100..103).
        assert "telemetry ietf subscription 100" in cfg  # CPU
        assert "telemetry ietf subscription 103" in cfg  # BGP
        assert "cpu-usage" in cfg and "memory-statistic" in cfg
        assert "bgp-state-data" in cfg and "environment-sensor" in cfg
        # Receiver + encoding.
        assert "receiver ip address 192.168.98.134 57400 protocol grpc-tcp" in cfg
        assert "encoding encode-kvgpb" in cfg

    def test_interval_centiseconds(self):
        d = _device()
        cfg = gs.generate_iosxe_gnmi(d, "1.1.1.1", _ifaces(d, ["Gi1"]))
        assert "update-policy periodic 3000" in cfg   # 30s interface/CPU
        assert "update-policy periodic 6000" in cfg   # 60s environment
        assert "update-policy periodic 10000" in cfg  # 100s BGP

    def test_no_interfaces_falls_back_to_all(self):
        d = _device()
        cfg = gs.generate_iosxe_gnmi(d, "1.1.1.1", [])
        assert "No monitored interfaces" in cfg
        # Falls back to the whole interfaces tree (no [name=...] predicate).
        assert "/interfaces-ios-xe-oper:interfaces" in cfg
        assert "interface[name=" not in cfg


class TestOtherCiscoPlatforms:
    def test_iosxr_uses_xr_xpaths(self):
        d = _device(platform="ios_xr", hostname="xr1", ip_address="10.0.0.9")
        cfg = gs.generate_iosxr_gnmi(d, "1.1.1.1", _ifaces(d, ["GigabitEthernet0/0/0/0"]))
        assert "Cisco-IOS-XR-wdsysmon-fd-oper" in cfg
        assert "infra-statistics/interfaces/interface[interface-name='GigabitEthernet0/0/0/0']" in cfg

    def test_nxos_generator(self):
        d = _device(platform="nxos", hostname="nx1", ip_address="10.0.0.8")
        cfg = gs.generate_nxos_gnmi(d, "1.1.1.1", _ifaces(d, ["Ethernet1/1"]))
        assert "telemetry ietf subscription 200" in cfg
        assert "Ethernet1/1" in cfg


class TestOpenConfigPlatforms:
    def test_junos_analytics_sensors(self):
        d = _device(platform="junos", hostname="j1", ip_address="10.0.0.7", management_ip="10.0.0.7")
        cfg = gs.generate_junos_gnmi(d, "1.1.1.1", _ifaces(d, ["ge-0/0/0"]))
        assert "set services analytics streaming-server NetPulse" in cfg
        assert "remote-port 57400" in cfg
        assert "/interfaces/interface[name='ge-0/0/0']/" in cfg
        assert "bgp/neighbors" in cfg

    def test_eos_terminattr(self):
        d = _device(platform="eos", hostname="e1", ip_address="10.0.0.6")
        cfg = gs.generate_eos_gnmi(d, "1.1.1.1", _ifaces(d, ["Ethernet1"]))
        assert "daemon TerminAttr" in cfg
        assert "gnmisub=oneof,1.1.1.1:57400" in cfg
        assert "/interfaces/interface[name='Ethernet1']/state/counters" in cfg


class TestNonGnmiPlatforms:
    def test_panos_otlp(self):
        d = _device(platform="panos", hostname="fw1", ip_address="10.0.0.5")
        cfg = gs.generate_push_config(d, "1.1.1.1", [])
        assert "OpenTelemetry" in cfg or "OTLP" in cfg
        assert "telemetry endpoint port 4317" in cfg

    def test_fortios_snmp(self):
        d = _device(platform="fortios", hostname="fg1", ip_address="10.0.0.4")
        cfg = gs.generate_push_config(d, "1.1.1.1", [])
        assert "config system snmp community" in cfg
        assert "set ip 1.1.1.1/32" in cfg

    def test_unknown_platform_generic_openconfig(self):
        d = _device(platform="weirdos", hostname="w1", ip_address="10.0.0.3")
        cfg = gs.generate_push_config(d, "1.1.1.1", _ifaces(d, ["eth0"]))
        assert "OpenConfig" in cfg
        assert "/interfaces/interface[name='eth0']/state/counters" in cfg


class TestConfigGenIntegration:
    def test_gnmi_section_is_targeted(self):
        d = _device()
        _ifaces(d, ["GigabitEthernet1"])
        out = config_gen.generate(d)
        gnmi = out["sections"]["gnmi"]["config"]
        assert "interface[name='GigabitEthernet1']" in gnmi
        assert "subscription 200" in gnmi

    def test_gnmi_section_present_for_panos(self):
        # Non-gNMI platform still gets a populated push-config section.
        d = _device(platform="panos", hostname="pa1", ip_address="10.9.9.9")
        out = config_gen.generate(d)
        assert out["sections"]["gnmi"]["config"]
        assert "4317" in out["sections"]["gnmi"]["config"]
