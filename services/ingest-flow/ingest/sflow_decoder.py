"""
sFlow v4/v5 binary packet decoder.

Parses Flow Sample records and extracts the embedded IP header to build
FlowRecord instances.  Counter samples are silently ignored — they're handled
by the SNMP poller, not the flow ingest path.

Wire format reference: https://sflow.org/developers/specifications.php
"""
from __future__ import annotations

import logging
import socket
import struct

from .models import FlowRecord

logger = logging.getLogger(__name__)

# ── Header sizes / magic numbers ──────────────────────────────────────────────
_ADDR_TYPE_IPV4 = 1
_ADDR_TYPE_IPV6 = 2

# Sample types
_SAMPLE_FLOW        = 1
_SAMPLE_COUNTER     = 2
_SAMPLE_FLOW_EXP    = 3   # expanded flow sample (v5)
_SAMPLE_COUNTER_EXP = 4

# Flow record formats
_FMT_RAW_HEADER = 1
_FMT_ETH        = 2
_FMT_IPV4       = 3
_FMT_IPV6       = 4

# Ethernet header protocols
_ETH_PROTO_IP  = 0x0800
_ETH_PROTO_IP6 = 0x86DD

# IP protocols
_IP_PROTO_TCP  = 6
_IP_PROTO_UDP  = 17
_IP_PROTO_ICMP = 1


class _Reader:
    """Minimal big-endian binary reader with bounds checking."""
    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos  = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos: self._pos + n]
        self._pos += n
        return chunk

    def uint32(self) -> int:
        raw = self.read(4)
        return struct.unpack("!I", raw)[0] if len(raw) == 4 else 0

    def uint16(self) -> int:
        raw = self.read(2)
        return struct.unpack("!H", raw)[0] if len(raw) == 2 else 0

    def skip(self, n: int) -> None:
        self._pos += n

    def ip4(self) -> str:
        raw = self.read(4)
        return socket.inet_ntoa(raw) if len(raw) == 4 else "0.0.0.0"

    def ip6(self) -> str:
        raw = self.read(16)
        return socket.inet_ntop(socket.AF_INET6, raw) if len(raw) == 16 else "::"

    def ipaddr(self) -> str:
        atype = self.uint32()
        if atype == _ADDR_TYPE_IPV4:
            return self.ip4()
        if atype == _ADDR_TYPE_IPV6:
            return self.ip6()
        return "0.0.0.0"

    # XDR strings are padded to 4-byte alignment
    def opaque(self, length: int) -> bytes:
        data = self.read(length)
        pad = (4 - length % 4) % 4
        self.skip(pad)
        return data


def decode(data: bytes, exporter_ip: str, recv_time: float) -> list[FlowRecord]:
    """
    Top-level decoder.  Returns all flow records from a single sFlow datagram.
    recv_time is wall-clock epoch seconds for when the packet arrived.
    """
    r = _Reader(data)
    if r.remaining < 4:
        return []

    version = r.uint32()
    if version not in (4, 5):
        logger.debug("unsupported sFlow version %d from %s", version, exporter_ip)
        return []

    agent_ip = r.ipaddr()
    if version == 5:
        _sub_agent_id = r.uint32()
    _seq  = r.uint32()
    uptime_ms = r.uint32()  # ms since agent boot
    num_samples = r.uint32()

    boot_epoch = recv_time - uptime_ms / 1000.0
    records: list[FlowRecord] = []

    for _ in range(num_samples):
        if r.remaining < 8:
            break
        sample_type = r.uint32()
        sample_len  = r.uint32()
        if r.remaining < sample_len:
            break
        sample_data = r.read(sample_len)
        if sample_type in (_SAMPLE_FLOW, _SAMPLE_FLOW_EXP):
            records.extend(_parse_flow_sample(
                sample_data, sample_type, exporter_ip, boot_epoch, recv_time
            ))
        # Counter samples silently ignored

    return records


def _parse_flow_sample(
    data: bytes,
    sample_type: int,
    exporter_ip: str,
    boot_epoch: float,
    recv_time: float,
) -> list[FlowRecord]:
    r = _Reader(data)

    _seq_num     = r.uint32()
    source_id    = r.uint32()
    sampling_rate = r.uint32()
    _sample_pool = r.uint32()
    _drops       = r.uint32()

    if sample_type == _SAMPLE_FLOW_EXP:
        # expanded: input/output are full 32-bit interface indexes
        in_if  = r.uint32()
        out_if = r.uint32()
    else:
        in_if  = r.uint32()
        out_if = r.uint32()

    num_records = r.uint32()
    records: list[FlowRecord] = []

    for _ in range(num_records):
        if r.remaining < 8:
            break
        enterprise_format = r.uint32()
        rec_len           = r.uint32()
        if r.remaining < rec_len:
            break
        rec_data = r.read(rec_len)
        # pad to 4-byte boundary is already consumed by read()

        enterprise = enterprise_format >> 12
        fmt        = enterprise_format & 0xFFF

        if enterprise != 0:
            continue  # vendor-specific, skip

        rec = _parse_flow_record(rec_data, fmt, exporter_ip, in_if, out_if,
                                 sampling_rate, boot_epoch, recv_time)
        if rec:
            records.append(rec)

    return records


def _parse_flow_record(
    data: bytes,
    fmt: int,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    boot_epoch: float,
    recv_time: float,
) -> FlowRecord | None:
    if fmt == _FMT_RAW_HEADER:
        return _parse_raw_header(data, exporter_ip, in_if, out_if,
                                  sampling_rate, recv_time)
    if fmt == _FMT_IPV4:
        return _parse_sampled_ipv4(data, exporter_ip, in_if, out_if,
                                    sampling_rate, recv_time)
    if fmt == _FMT_IPV6:
        return _parse_sampled_ipv6(data, exporter_ip, in_if, out_if,
                                    sampling_rate, recv_time)
    # FMT_ETH: we'll try to parse if it looks like IP-over-Ethernet
    return None


def _parse_raw_header(
    data: bytes,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    recv_time: float,
) -> FlowRecord | None:
    r = _Reader(data)
    header_protocol = r.uint32()
    frame_length    = r.uint32()
    stripped        = r.uint32()
    header_size     = r.uint32()
    header          = r.read(header_size)

    # 1=ETHERNET_ISO88023, 11=IPv4, 14=IPv6
    if header_protocol == 11:
        return _parse_ip4_bytes(header, exporter_ip, in_if, out_if,
                                 sampling_rate, frame_length, recv_time)
    if header_protocol == 1:
        # Ethernet: skip 14-byte L2 header (dst[6]+src[6]+ethertype[2])
        if len(header) < 14:
            return None
        ethertype = struct.unpack_from("!H", header, 12)[0]
        if ethertype == _ETH_PROTO_IP:
            return _parse_ip4_bytes(header[14:], exporter_ip, in_if, out_if,
                                     sampling_rate, frame_length, recv_time)
        if ethertype == _ETH_PROTO_IP6:
            return _parse_ip6_bytes(header[14:], exporter_ip, in_if, out_if,
                                     sampling_rate, frame_length, recv_time)
    return None


def _parse_ip4_bytes(
    data: bytes,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    frame_length: int,
    recv_time: float,
) -> FlowRecord | None:
    if len(data) < 20:
        return None
    ihl = (data[0] & 0x0F) * 4
    tos     = data[1]
    proto   = data[9]
    src_ip  = socket.inet_ntoa(data[12:16])
    dst_ip  = socket.inet_ntoa(data[16:20])
    src_port = 0
    dst_port = 0
    tcp_flags = 0
    if proto in (_IP_PROTO_TCP, _IP_PROTO_UDP) and len(data) >= ihl + 4:
        src_port, dst_port = struct.unpack_from("!HH", data, ihl)
    if proto == _IP_PROTO_TCP and len(data) >= ihl + 14:
        tcp_flags = data[ihl + 13]

    return FlowRecord(
        exporter_ip=exporter_ip,
        exporter_port=0,
        protocol_version="sflow5",
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        ip_protocol=proto,
        abs_start_time=recv_time,
        abs_end_time=recv_time,
        duration_ms=0.0,
        packets=sampling_rate,
        bytes_count=frame_length * sampling_rate,
        input_if=in_if,
        output_if=out_if,
        tcp_flags=tcp_flags,
        tos=tos,
    )


def _parse_ip6_bytes(
    data: bytes,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    frame_length: int,
    recv_time: float,
) -> FlowRecord | None:
    # IPv6 fixed header = 40 bytes
    if len(data) < 40:
        return None
    proto   = data[6]
    src_ip  = socket.inet_ntop(socket.AF_INET6, data[8:24])
    dst_ip  = socket.inet_ntop(socket.AF_INET6, data[24:40])
    src_port = 0
    dst_port = 0
    if proto in (_IP_PROTO_TCP, _IP_PROTO_UDP) and len(data) >= 44:
        src_port, dst_port = struct.unpack_from("!HH", data, 40)

    return FlowRecord(
        exporter_ip=exporter_ip,
        exporter_port=0,
        protocol_version="sflow5",
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        ip_protocol=proto,
        abs_start_time=recv_time,
        abs_end_time=recv_time,
        duration_ms=0.0,
        packets=sampling_rate,
        bytes_count=frame_length * sampling_rate,
        input_if=in_if,
        output_if=out_if,
    )


def _parse_sampled_ipv4(
    data: bytes,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    recv_time: float,
) -> FlowRecord | None:
    # Sampled IPv4: length(4), proto(4), src(4), dst(4), sport(4), dport(4), tcp_flags(4), tos(4)
    if len(data) < 32:
        return None
    r = _Reader(data)
    frame_length = r.uint32()
    proto        = r.uint32()
    src_ip       = r.ip4()
    dst_ip       = r.ip4()
    src_port     = r.uint32()
    dst_port     = r.uint32()
    tcp_flags    = r.uint32()
    tos          = r.uint32()

    return FlowRecord(
        exporter_ip=exporter_ip,
        exporter_port=0,
        protocol_version="sflow5",
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        ip_protocol=proto,
        abs_start_time=recv_time,
        abs_end_time=recv_time,
        duration_ms=0.0,
        packets=sampling_rate,
        bytes_count=frame_length * sampling_rate,
        input_if=in_if,
        output_if=out_if,
        tcp_flags=tcp_flags,
        tos=tos,
    )


def _parse_sampled_ipv6(
    data: bytes,
    exporter_ip: str,
    in_if: int,
    out_if: int,
    sampling_rate: int,
    recv_time: float,
) -> FlowRecord | None:
    # Sampled IPv6: length(4), proto(4), src(16), dst(16), sport(4), dport(4), priority(4)
    if len(data) < 52:
        return None
    r = _Reader(data)
    frame_length = r.uint32()
    proto        = r.uint32()
    src_ip       = r.ip6()
    dst_ip       = r.ip6()
    src_port     = r.uint32()
    dst_port     = r.uint32()
    _priority    = r.uint32()

    return FlowRecord(
        exporter_ip=exporter_ip,
        exporter_port=0,
        protocol_version="sflow5",
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        ip_protocol=proto,
        abs_start_time=recv_time,
        abs_end_time=recv_time,
        duration_ms=0.0,
        packets=sampling_rate,
        bytes_count=frame_length * sampling_rate,
        input_if=in_if,
        output_if=out_if,
    )
