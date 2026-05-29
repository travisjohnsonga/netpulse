"""Tests for ingest.sflow_decoder — no external dependencies."""
from __future__ import annotations

import socket
import struct
import time

import pytest
from ingest.sflow_decoder import decode


# ── wire-building helpers ─────────────────────────────────────────────────────

def _ipv4_header(
    src="10.0.0.1", dst="10.0.0.2",
    proto=6, sp=1234, dp=80,
) -> bytes:
    """Minimal 20-byte IPv4 header (no options)."""
    return bytes([
        0x45,           # version=4, IHL=5
        0,              # DSCP/ECN
        0, 40,          # total length
        0, 1,           # ID
        0, 0,           # flags/frag offset
        64,             # TTL
        proto,          # protocol
        0, 0,           # checksum (ignored)
    ]) + socket.inet_aton(src) + socket.inet_aton(dst) + struct.pack("!HH", sp, dp)


def _raw_header_record(ip_hdr: bytes) -> bytes:
    """Build a sFlow Raw Packet Header flow record."""
    # Enterprise=0, format=1 → combined word = 1
    header_size = len(ip_hdr)
    padded = ip_hdr + b"\x00" * ((4 - len(ip_hdr) % 4) % 4)
    rec_body = struct.pack("!IIII", 11, len(ip_hdr) + 20, 0, header_size) + padded
    length = len(rec_body)
    return struct.pack("!II", 1, length) + rec_body


def _sampled_ipv4_record(
    src="10.0.0.1", dst="10.0.0.2",
    proto=6, sp=1234, dp=80,
    frame_length=1500,
) -> bytes:
    """Build a sFlow Sampled IPv4 flow record (format=3)."""
    rec_body = (
        struct.pack("!I", frame_length) +
        struct.pack("!I", proto) +
        socket.inet_aton(src) +
        socket.inet_aton(dst) +
        struct.pack("!IIII", sp, dp, 0, 0)  # sport, dport, tcp_flags, tos
    )
    return struct.pack("!II", 3, len(rec_body)) + rec_body


def _flow_sample(
    seq: int,
    sampling_rate: int,
    in_if: int,
    out_if: int,
    flow_records: list[bytes],
) -> bytes:
    """Build a sFlow v5 Flow Sample."""
    records_bytes = b"".join(flow_records)
    body = struct.pack(
        "!IIIIIII",
        seq,
        1,                  # source_id
        sampling_rate,
        sampling_rate * 10, # sample_pool
        0,                  # drops
        in_if,
        out_if,
    ) + struct.pack("!I", len(flow_records)) + records_bytes
    # Sample type=1 (Flow Sample)
    return struct.pack("!II", 1, len(body)) + body


def _sflow_v5_packet(samples: list[bytes], agent_ip: str = "192.168.1.1") -> bytes:
    """Build a minimal sFlow v5 datagram."""
    samples_bytes = b"".join(samples)
    hdr = (
        struct.pack("!I", 5) +                      # version
        struct.pack("!I", 1) +                      # agent_address_type = IPv4
        socket.inet_aton(agent_ip) +                # agent IP
        struct.pack("!I", 0) +                      # sub_agent_id
        struct.pack("!I", 1) +                      # sequence_number
        struct.pack("!I", 60000) +                  # uptime ms
        struct.pack("!I", len(samples))             # num_samples
    )
    return hdr + samples_bytes


# ── tests ─────────────────────────────────────────────────────────────────────

class TestSFlowV5RawHeader:
    def _pkt(self, src="10.0.0.1", dst="10.0.0.2", proto=6, sp=1234, dp=80):
        ip_hdr = _ipv4_header(src=src, dst=dst, proto=proto, sp=sp, dp=dp)
        rh_rec = _raw_header_record(ip_hdr)
        sample = _flow_sample(1, 1000, in_if=1, out_if=2, flow_records=[rh_rec])
        return _sflow_v5_packet([sample])

    def test_basic_decode(self):
        records = decode(self._pkt(), "192.168.1.1", time.time())
        assert len(records) == 1
        r = records[0]
        assert r.src_ip == "10.0.0.1"
        assert r.dst_ip == "10.0.0.2"
        assert r.ip_protocol == 6
        assert r.src_port == 1234
        assert r.dst_port == 80

    def test_protocol_version_tag(self):
        records = decode(self._pkt(), "192.168.1.1", time.time())
        assert records[0].protocol_version == "sflow5"

    def test_exporter_ip(self):
        records = decode(self._pkt(), "10.5.5.5", time.time())
        assert records[0].exporter_ip == "10.5.5.5"

    def test_sampling_rate_applied_to_packets(self):
        ip_hdr = _ipv4_header()
        rh_rec = _raw_header_record(ip_hdr)
        sample = _flow_sample(1, 512, in_if=1, out_if=2, flow_records=[rh_rec])
        pkt    = _sflow_v5_packet([sample])
        records = decode(pkt, "192.168.1.1", time.time())
        assert records[0].packets == 512

    def test_udp_proto(self):
        records = decode(self._pkt(proto=17, sp=53, dp=12345), "10.0.0.1", time.time())
        assert records[0].ip_protocol == 17
        assert records[0].src_port == 53
        assert records[0].dst_port == 12345

    def test_interface_indices(self):
        ip_hdr = _ipv4_header()
        rh_rec = _raw_header_record(ip_hdr)
        sample = _flow_sample(1, 100, in_if=5, out_if=7, flow_records=[rh_rec])
        pkt    = _sflow_v5_packet([sample])
        r = decode(pkt, "192.168.1.1", time.time())[0]
        assert r.input_if == 5
        assert r.output_if == 7

    def test_five_tuple(self):
        records = decode(self._pkt(proto=6, sp=1234, dp=80), "10.0.0.1", time.time())
        assert records[0].five_tuple() == ("10.0.0.1", "10.0.0.2", 1234, 80, 6)


class TestSFlowV5SampledIPv4:
    def _pkt(self, src="10.1.1.1", dst="10.2.2.2", proto=6):
        rec    = _sampled_ipv4_record(src=src, dst=dst, proto=proto)
        sample = _flow_sample(1, 2048, in_if=1, out_if=2, flow_records=[rec])
        return _sflow_v5_packet([sample])

    def test_decode(self):
        records = decode(self._pkt(), "192.168.1.1", time.time())
        assert len(records) == 1
        r = records[0]
        assert r.src_ip == "10.1.1.1"
        assert r.dst_ip == "10.2.2.2"
        assert r.ip_protocol == 6

    def test_sampling_rate(self):
        records = decode(self._pkt(), "192.168.1.1", time.time())
        assert records[0].packets == 2048

    def test_multiple_samples(self):
        rec1 = _sampled_ipv4_record(src="1.1.1.1", dst="2.2.2.2")
        rec2 = _sampled_ipv4_record(src="3.3.3.3", dst="4.4.4.4")
        s1 = _flow_sample(1, 100, 1, 2, [rec1])
        s2 = _flow_sample(2, 100, 1, 2, [rec2])
        pkt = _sflow_v5_packet([s1, s2])
        records = decode(pkt, "192.168.1.1", time.time())
        assert len(records) == 2
        ips = {r.src_ip for r in records}
        assert "1.1.1.1" in ips
        assert "3.3.3.3" in ips


class TestSFlowEdgeCases:
    def test_empty_bytes(self):
        assert decode(b"", "1.1.1.1", time.time()) == []

    def test_wrong_version(self):
        pkt = struct.pack("!I", 3) + b"\x00" * 30
        assert decode(pkt, "1.1.1.1", time.time()) == []

    def test_no_samples(self):
        hdr = (
            struct.pack("!I", 5) +
            struct.pack("!I", 1) +
            socket.inet_aton("10.0.0.1") +
            struct.pack("!IIII", 0, 1, 60000, 0)
        )
        assert decode(hdr, "10.0.0.1", time.time()) == []
