"""Tests for ingest.mib_resolver — no external dependencies required."""
import pytest
from ingest.mib_resolver import display_name, resolve


class TestResolveExact:
    def test_sys_descr(self):
        mib, name, inst = resolve("1.3.6.1.2.1.1.1")
        assert mib == "SNMPv2-MIB"
        assert name == "sysDescr"
        assert inst == ""

    def test_sys_uptime_scalar(self):
        mib, name, inst = resolve("1.3.6.1.2.1.1.3")
        assert name == "sysUpTime"

    def test_snmp_trap_oid(self):
        mib, name, inst = resolve("1.3.6.1.6.3.1.1.4.1.0")
        assert name == "snmpTrapOID.0"

    def test_link_down_trap(self):
        mib, name, inst = resolve("1.3.6.1.6.3.1.1.5.3")
        assert name == "linkDown"
        assert mib == "SNMPv2-MIB"

    def test_ups_trap_on_battery(self):
        mib, name, inst = resolve("1.3.6.1.2.1.33.2.0.1")
        assert mib == "UPS-MIB"
        assert name == "upsTrapOnBattery"

    def test_apc_on_battery_trap(self):
        mib, name, inst = resolve("1.3.6.1.4.1.318.0.9")
        assert mib == "APC-POWERNET-MIB"
        assert name == "apcUpsOnBattery"

    def test_bgp_established_trap(self):
        mib, name, inst = resolve("1.3.6.1.2.1.15.7.1")
        assert mib == "BGP4-MIB"
        assert name == "bgpEstablished"


class TestResolveWithInstance:
    def test_if_in_octets_instance_1(self):
        mib, name, inst = resolve("1.3.6.1.2.1.2.2.1.10.1")
        assert mib == "IF-MIB"
        assert name == "ifInOctets"
        assert inst == "1"

    def test_if_in_octets_instance_42(self):
        mib, name, inst = resolve("1.3.6.1.2.1.2.2.1.10.42")
        assert inst == "42"

    def test_if_hc_in_octets_with_instance(self):
        mib, name, inst = resolve("1.3.6.1.2.1.31.1.1.1.6.7")
        assert name == "ifHCInOctets"
        assert inst == "7"

    def test_bgp_peer_state_with_ip_instance(self):
        # bgpPeerState table indexed by peer IP
        mib, name, inst = resolve("1.3.6.1.2.1.15.3.1.2.10.0.0.1")
        assert name == "bgpPeerState"
        assert inst == "10.0.0.1"

    def test_ups_output_percent_load_with_line(self):
        mib, name, inst = resolve("1.3.6.1.2.1.33.1.4.4.1.5.1")
        assert name == "upsOutputPercentLoad"
        assert inst == "1"

    def test_apc_battery_capacity_scalar(self):
        # .0 suffix is an instance here
        mib, name, inst = resolve("1.3.6.1.4.1.318.1.1.1.2.2.1.0")
        assert name == "upsAdvBatteryCapacity"
        assert inst == "0"


class TestUnknownOID:
    def test_unknown_returns_oid_unchanged(self):
        mib, name, inst = resolve("1.99.99.99.99")
        assert mib == "unknown"
        assert name == "1.99.99.99.99"
        assert inst == ""

    def test_partial_match_fails_gracefully(self):
        # Not in the table at any prefix depth
        mib, name, inst = resolve("1.3.6.1.99")
        assert mib == "unknown"


class TestDisplayName:
    def test_known_scalar(self):
        assert display_name("1.3.6.1.2.1.1.5") == "SNMPv2-MIB::sysName"

    def test_table_column_with_instance(self):
        label = display_name("1.3.6.1.2.1.2.2.1.16.3")
        assert label == "IF-MIB::ifOutOctets.3"

    def test_unknown_oid_returned_verbatim(self):
        assert display_name("9.9.9.9") == "9.9.9.9"

    def test_trap_oid(self):
        assert display_name("1.3.6.1.6.3.1.1.5.3") == "SNMPv2-MIB::linkDown"

    def test_apc_trap(self):
        label = display_name("1.3.6.1.4.1.318.0.5")
        assert "apcUpsLowBattery" in label


class TestMIBCoverage:
    """Spot-checks that all four required MIBs have entries."""

    def test_if_mib_present(self):
        mib, _, _ = resolve("1.3.6.1.2.1.2.2.1.8")
        assert mib == "IF-MIB"

    def test_bgp4_mib_present(self):
        mib, _, _ = resolve("1.3.6.1.2.1.15.2")
        assert mib == "BGP4-MIB"

    def test_ups_mib_present(self):
        mib, _, _ = resolve("1.3.6.1.2.1.33.1.2.4")
        assert mib == "UPS-MIB"

    def test_apc_mib_present(self):
        mib, _, _ = resolve("1.3.6.1.4.1.318.1.1.1.4.2.3")
        assert mib == "APC-POWERNET-MIB"
