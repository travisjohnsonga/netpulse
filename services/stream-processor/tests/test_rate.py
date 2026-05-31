"""
Tests for interface counter-delta → bps/pps rate conversion.
"""
from unittest.mock import MagicMock

from stream_processor.rate import RateCalculator, classify_counter
from stream_processor.handlers import metrics as metric_handler


def _snmp(name, value):
    return {name: {"name": name, "value": value, "type": "Counter64"}}


class TestClassifyCounter:
    def test_snmp_octets(self):
        assert classify_counter("ifHCInOctets") == ("in_bps", 8.0)
        assert classify_counter("ifHCOutOctets") == ("out_bps", 8.0)
        assert classify_counter("ifInOctets") == ("in_bps", 8.0)

    def test_gnmi_octets(self):
        assert classify_counter("in-octets") == ("in_bps", 8.0)
        assert classify_counter("out-octets") == ("out_bps", 8.0)

    def test_packets(self):
        assert classify_counter("ifInUcastPkts") == ("in_pps", 1.0)
        assert classify_counter("out-unicast-pkts") == ("out_pps", 1.0)

    def test_non_rate_counters_ignored(self):
        assert classify_counter("ifInErrors") is None
        assert classify_counter("ifOutDiscards") is None
        assert classify_counter("sysUpTime") is None


class TestRateCalculator:
    def test_first_sample_emits_nothing(self):
        rc = RateCalculator()
        out = rc.compute("d1", _snmp("ifHCInOctets.5", 1000), ts=100.0)
        assert out == {}

    def test_second_sample_computes_bps(self):
        rc = RateCalculator()
        rc.compute("d1", _snmp("ifHCInOctets.5", 1000), ts=100.0)
        out = rc.compute("d1", _snmp("ifHCInOctets.5", 2000), ts=110.0)
        # (2000-1000) octets * 8 / 10s = 800 bps
        assert out == {"5_in_bps": 800.0}

    def test_pps_computed(self):
        rc = RateCalculator()
        rc.compute("d1", _snmp("ifInUcastPkts.3", 100), ts=0.0)
        out = rc.compute("d1", _snmp("ifInUcastPkts.3", 400), ts=10.0)
        # (400-100) pkts / 10s = 30 pps
        assert out == {"3_in_pps": 30.0}

    def test_gnmi_name_format(self):
        rc = RateCalculator()
        m1 = {"GigabitEthernet1/in-octets": {"name": "GigabitEthernet1/in-octets", "value": 0}}
        m2 = {"GigabitEthernet1/in-octets": {"name": "GigabitEthernet1/in-octets", "value": 1250}}
        rc.compute("d1", m1, ts=0.0)
        out = rc.compute("d1", m2, ts=10.0)
        # 1250 * 8 / 10 = 1000 bps
        assert out == {"GigabitEthernet1_in_bps": 1000.0}

    def test_counter_reset_skipped(self):
        rc = RateCalculator()
        rc.compute("d1", _snmp("ifHCInOctets.1", 5000), ts=0.0)
        out = rc.compute("d1", _snmp("ifHCInOctets.1", 100), ts=10.0)  # reboot
        assert out == {}

    def test_non_positive_dt_skipped(self):
        rc = RateCalculator()
        rc.compute("d1", _snmp("ifHCInOctets.1", 100), ts=10.0)
        out = rc.compute("d1", _snmp("ifHCInOctets.1", 200), ts=10.0)
        assert out == {}

    def test_devices_isolated(self):
        rc = RateCalculator()
        rc.compute("d1", _snmp("ifHCInOctets.1", 1000), ts=0.0)
        # Different device, same counter — no cross-talk, first sample for d2.
        out = rc.compute("d2", _snmp("ifHCInOctets.1", 9999), ts=10.0)
        assert out == {}

    def test_string_value_parsed(self):
        rc = RateCalculator()
        rc.compute("d1", {"o": {"name": "ifHCInOctets.1", "value": "1000"}}, ts=0.0)
        out = rc.compute("d1", {"o": {"name": "ifHCInOctets.1", "value": "2000"}}, ts=10.0)
        assert out == {"1_in_bps": 800.0}


class TestHandlerIntegration:
    def test_rate_fields_added_to_influx_write(self):
        rc = RateCalculator()
        influx = MagicMock()
        d1 = {"timestamp": "2026-01-01T00:00:00+00:00", "metrics": _snmp("ifHCInOctets.5", 1000)}
        d2 = {"timestamp": "2026-01-01T00:00:10+00:00", "metrics": _snmp("ifHCInOctets.5", 2000)}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.r1.metrics", d1, influx, rc)
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.r1.metrics", d2, influx, rc)
        _, _, fields = influx.write.call_args[0]
        assert fields["5_in_bps"] == 800.0
        # Raw counter still written too.
        assert fields["ifHCInOctets_5"] == 2000.0

    def test_no_rate_calc_is_backward_compatible(self):
        influx = MagicMock()
        data = {"metrics": _snmp("ifHCInOctets.5", 2000)}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.r1.metrics", data, influx)
        _, _, fields = influx.write.call_args[0]
        assert "ifHCInOctets_5" in fields
        assert "5_in_bps" not in fields
