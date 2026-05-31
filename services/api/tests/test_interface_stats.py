import asyncio


def _cmd():
    from apps.telemetry.management.commands.run_stream_processor import Command
    cmd = Command()
    cmd._iface_prev = {}
    writes = []
    async def cap(measurement, tags, fields, timestamp=None):
        writes.append((measurement, tags, fields))
    cmd._write_influx = cap
    return cmd, writes


class TestCounterDelta:
    def test_normal(self):
        from apps.telemetry.management.commands.run_stream_processor import Command
        assert Command._counter_delta(1500, 1000, 2**64) == 500

    def test_rollover(self):
        from apps.telemetry.management.commands.run_stream_processor import Command
        # 32-bit counter wrapped: prev near max, cur small
        d = Command._counter_delta(10, 2**32 - 5, 2**32)
        assert d == 15


class TestInterfaceStats:
    def test_bps_pps_util(self):
        cmd, writes = _cmd()
        f1 = {"ifHCInOctets_2": 1000, "ifHCOutOctets_2": 500,
              "ifHCInUcastPkts_2": 100, "ifHCOutUcastPkts_2": 50,
              "ifHighSpeed_2": 1000, "ifOperStatus_2": 1}
        # +1_250_000 in octets and +125_000 out octets over 10s
        f2 = {"ifHCInOctets_2": 1000 + 1_250_000, "ifHCOutOctets_2": 500 + 125_000,
              "ifHCInUcastPkts_2": 100 + 1980, "ifHCOutUcastPkts_2": 50 + 10,
              "ifHighSpeed_2": 1000, "ifOperStatus_2": 1}
        asyncio.run(cmd._interface_stats("3", f1, "2026-05-30T20:00:00+00:00"))
        asyncio.run(cmd._interface_stats("3", f2, "2026-05-30T20:00:10+00:00"))
        m, tags, fields = writes[-1]
        assert m == "interface_stats"
        assert tags == {"device_id": "3", "if_index": "2"}
        assert fields["in_bps"] == 1_000_000.0      # 1.25M octets/10s*8
        assert fields["out_bps"] == 100_000.0
        assert fields["in_pps"] == 198.0
        assert fields["in_util_pct"] == 0.1          # 1Mbps / 1Gbps
        assert fields["oper_status"] == 1

    def test_first_sample_has_no_rates(self):
        cmd, writes = _cmd()
        asyncio.run(cmd._interface_stats("3", {"ifHCInOctets_2": 1000, "ifOperStatus_2": 1},
                                         "2026-05-30T20:00:00+00:00"))
        # first sample → only status written, no bps
        assert all("in_bps" not in f for _, _, f in writes)
        assert ("3", "2") in cmd._iface_prev

    def test_raw_oid_fields_also_parsed(self):
        cmd, writes = _cmd()
        # ifHCInOctets raw OID base = 1_3_6_1_2_1_31_1_1_1_6, index 4
        f1 = {"1_3_6_1_2_1_31_1_1_1_6_4": 0}
        f2 = {"1_3_6_1_2_1_31_1_1_1_6_4": 1000}
        asyncio.run(cmd._interface_stats("3", f1, "2026-05-30T20:00:00+00:00"))
        asyncio.run(cmd._interface_stats("3", f2, "2026-05-30T20:00:10+00:00"))
        assert writes[-1][2]["in_bps"] == 800.0   # 1000 octets/10s*8


class TestGnmiInterfaceStats:
    """gNMI/MDT counters arrive as '<InterfaceName>/<leaf>', not '<base>_<ifindex>'."""

    def test_gnmi_format_parsed_and_tagged_by_name(self):
        cmd, writes = _cmd()
        f1 = {"GigabitEthernet1/in_octets": 1000, "GigabitEthernet1/out_octets": 500,
              "GigabitEthernet1/in_unicast_pkts": 100, "GigabitEthernet1/speed": 1000}
        f2 = {"GigabitEthernet1/in_octets": 1000 + 1_250_000,
              "GigabitEthernet1/out_octets": 500 + 125_000,
              "GigabitEthernet1/in_unicast_pkts": 100 + 1980,
              "GigabitEthernet1/speed": 1000}
        asyncio.run(cmd._interface_stats("3", f1, "2026-05-30T20:00:00+00:00"))
        asyncio.run(cmd._interface_stats("3", f2, "2026-05-30T20:00:10+00:00"))
        m, tags, fields = writes[-1]
        assert m == "interface_stats"
        # gNMI rows carry both if_index (the name) AND an explicit if_name tag.
        assert tags == {"device_id": "3", "if_index": "GigabitEthernet1", "if_name": "GigabitEthernet1"}
        assert fields["in_bps"] == 1_000_000.0
        assert fields["out_bps"] == 100_000.0
        assert fields["in_pps"] == 198.0
        assert fields["in_util_pct"] == 0.1   # speed 1000 Mbps → 1Gbps

    def test_gnmi_interface_name_with_slashes(self):
        cmd, writes = _cmd()
        # IOS-XR style name with embedded slashes — must split on the LAST slash.
        f1 = {"GigabitEthernet0/0/0/in_octets": 0}
        f2 = {"GigabitEthernet0/0/0/in_octets": 1000}
        asyncio.run(cmd._interface_stats("9", f1, "2026-05-30T20:00:00+00:00"))
        asyncio.run(cmd._interface_stats("9", f2, "2026-05-30T20:00:10+00:00"))
        m, tags, fields = writes[-1]
        assert tags == {"device_id": "9", "if_index": "GigabitEthernet0/0/0", "if_name": "GigabitEthernet0/0/0"}
        assert fields["in_bps"] == 800.0

    def test_snmp_rows_have_no_if_name_tag(self):
        # SNMP buckets are numeric ifIndex and must NOT get an if_name tag here.
        cmd, writes = _cmd()
        asyncio.run(cmd._interface_stats("3", {"ifHCInOctets_2": 0}, "2026-05-30T20:00:00+00:00"))
        asyncio.run(cmd._interface_stats("3", {"ifHCInOctets_2": 1000}, "2026-05-30T20:00:10+00:00"))
        _, tags, _ = writes[-1]
        assert tags == {"device_id": "3", "if_index": "2"}
        assert "if_name" not in tags
