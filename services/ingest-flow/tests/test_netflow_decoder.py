"""Tests for ingest.netflow_decoder — no external dependencies."""
from __future__ import annotations

import socket
import struct
import time

import pytest
from ingest.netflow_decoder import NetFlowDecoder


def _ip4(s: str) -> bytes:
    return socket.inet_aton(s)


# ── helpers to build minimal wire packets ─────────────────────────────────────

def _v5_packet(
    src="10.0.0.1", dst="10.0.0.2",
    sp=1024, dp=80, proto=6,
    pkts=10, octets=5000,
    first_up=1000, last_up=2000,   # uptime ms
    sys_uptime=60000,              # router uptime ms
    unix_secs=None,
) -> bytes:
    if unix_secs is None:
        unix_secs = int(time.time())
    hdr = struct.pack(
        "!HHIIIIHH",
        5,           # version
        1,           # count
        sys_uptime,  # sysUptime ms
        unix_secs,   # unix_secs
        0,           # unix_nsecs
        1,           # seq
        0, 0,        # engine_type, engine_id, sampling
    )
    rec = struct.pack(
        "!4s4s4sHHIIIIHHBBBBHHBBH",
        _ip4(src), _ip4(dst), _ip4("0.0.0.0"),
        1, 2,            # input, output if
        pkts, octets,
        first_up, last_up,
        sp, dp,
        0,               # pad
        0x12,            # tcp_flags (SYN+ACK)
        proto, 0,        # proto, tos
        65000, 65001,    # src_as, dst_as
        24, 24, 0,       # masks, pad
    )
    return hdr + rec


def _v9_template_flowset(template_id: int, fields: list[tuple[int, int]]) -> bytes:
    field_bytes = b"".join(struct.pack("!HH", ft, fl) for ft, fl in fields)
    tmpl = struct.pack("!HH", template_id, len(fields)) + field_bytes
    # FlowSet header: id=0, length
    length = 4 + len(tmpl)
    return struct.pack("!HH", 0, length) + tmpl


def _v9_data_flowset(template_id: int, records_bytes: bytes) -> bytes:
    length = 4 + len(records_bytes)
    return struct.pack("!HH", template_id, length) + records_bytes


def _v9_packet(source_id: int, flowsets: list[bytes], unix_secs: int | None = None) -> bytes:
    if unix_secs is None:
        unix_secs = int(time.time())
    body = b"".join(flowsets)
    hdr = struct.pack(
        "!HHIIII",
        9,         # version
        len(flowsets),
        60000,     # sysUptime ms
        unix_secs,
        1,         # seq
        source_id,
    )
    return hdr + body


# ── NetFlow v5 tests ──────────────────────────────────────────────────────────

class TestNetFlowV5:
    def _decode(self, data: bytes) -> list:
        return NetFlowDecoder("192.168.1.1").decode(data)

    def test_basic_decode(self):
        pkt = _v5_packet()
        records = self._decode(pkt)
        assert len(records) == 1
        r = records[0]
        assert r.src_ip == "10.0.0.1"
        assert r.dst_ip == "10.0.0.2"
        assert r.src_port == 1024
        assert r.dst_port == 80
        assert r.ip_protocol == 6
        assert r.packets == 10
        assert r.bytes_count == 5000

    def test_protocol_version_tag(self):
        records = self._decode(_v5_packet())
        assert records[0].protocol_version == "netflow5"

    def test_exporter_ip(self):
        dec = NetFlowDecoder("1.2.3.4")
        records = dec.decode(_v5_packet())
        assert records[0].exporter_ip == "1.2.3.4"

    def test_five_tuple(self):
        records = self._decode(_v5_packet(proto=17))
        assert records[0].five_tuple() == ("10.0.0.1", "10.0.0.2", 1024, 80, 17)

    def test_duration_ms(self):
        # first_up=1000, last_up=2000 → 1000 ms duration
        records = self._decode(_v5_packet(first_up=1000, last_up=2000))
        assert records[0].duration_ms == pytest.approx(1000.0)

    def test_absolute_timestamps_sane(self):
        now = int(time.time())
        records = self._decode(_v5_packet(unix_secs=now, sys_uptime=60000,
                                           first_up=55000, last_up=60000))
        r = records[0]
        # abs_start should be ~ now - 5 seconds
        assert abs(r.abs_start_time - (now - 5)) < 1.0
        assert r.abs_end_time >= r.abs_start_time

    def test_tcp_flags(self):
        records = self._decode(_v5_packet())
        assert records[0].tcp_flags == 0x12   # SYN+ACK

    def test_as_numbers(self):
        records = self._decode(_v5_packet())
        assert records[0].src_as == 65000
        assert records[0].dst_as == 65001

    def test_truncated_packet_returns_empty(self):
        assert self._decode(b"\x00\x05") == []

    def test_unknown_version_returns_empty(self):
        bad = struct.pack("!H", 99) + b"\x00" * 22
        assert self._decode(bad) == []

    def test_empty_bytes(self):
        assert self._decode(b"") == []

    def test_to_dict_keys(self):
        r = self._decode(_v5_packet())[0]
        d = r.to_dict()
        for key in ("src_ip", "dst_ip", "src_port", "dst_port",
                    "ip_protocol", "packets", "bytes", "exporter_ip"):
            assert key in d


# ── NetFlow v9 tests ──────────────────────────────────────────────────────────

class TestNetFlowV9:
    # Field types
    _FIELDS = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (2, 4), (1, 4), (22, 4), (21, 4)]
    # src_ip, dst_ip, src_port, dst_port, proto, pkts, bytes, start_uptime, end_uptime

    def _build_data_record(
        self, src="10.1.1.1", dst="10.2.2.2",
        sp=443, dp=50000, proto=6,
        pkts=5, octets=1200,
        start_up=10000, end_up=11000,
    ) -> bytes:
        return (
            socket.inet_aton(src) +
            socket.inet_aton(dst) +
            struct.pack("!HH", sp, dp) +
            struct.pack("!B", proto) +
            struct.pack("!I", pkts) +
            struct.pack("!I", octets) +
            struct.pack("!II", start_up, end_up)
        )

    def _make_packet(self, unix_secs: int | None = None) -> bytes:
        tmpl_fs  = _v9_template_flowset(256, self._FIELDS)
        data_rec = self._build_data_record()
        data_fs  = _v9_data_flowset(256, data_rec)
        return _v9_packet(source_id=1, flowsets=[tmpl_fs, data_fs], unix_secs=unix_secs)

    def test_template_then_data(self):
        dec = NetFlowDecoder("192.168.0.1")
        records = dec.decode(self._make_packet())
        assert len(records) == 1
        r = records[0]
        assert r.src_ip == "10.1.1.1"
        assert r.dst_ip == "10.2.2.2"
        assert r.src_port == 443
        assert r.dst_port == 50000
        assert r.ip_protocol == 6
        assert r.packets == 5
        assert r.bytes_count == 1200

    def test_data_before_template_yields_nothing(self):
        dec = NetFlowDecoder("192.168.0.1")
        data_rec = self._build_data_record()
        data_fs  = _v9_data_flowset(256, data_rec)
        pkt = _v9_packet(source_id=1, flowsets=[data_fs])
        # No template yet → decoder skips data flowset
        assert dec.decode(pkt) == []

    def test_template_persists_across_packets(self):
        dec = NetFlowDecoder("192.168.0.1")
        tmpl_pkt = _v9_packet(source_id=1, flowsets=[_v9_template_flowset(256, self._FIELDS)])
        dec.decode(tmpl_pkt)   # populate template cache

        data_rec = self._build_data_record(proto=17)
        data_pkt = _v9_packet(source_id=1, flowsets=[_v9_data_flowset(256, data_rec)])
        records  = dec.decode(data_pkt)
        assert len(records) == 1
        assert records[0].ip_protocol == 17

    def test_protocol_version_tag(self):
        dec = NetFlowDecoder("192.168.0.1")
        records = dec.decode(self._make_packet())
        assert records[0].protocol_version == "netflow9"

    def test_duration_from_uptime(self):
        # start_up=10000, end_up=11000 → 1000 ms
        dec = NetFlowDecoder("192.168.0.1")
        records = dec.decode(self._make_packet())
        assert records[0].duration_ms == pytest.approx(1000.0)
