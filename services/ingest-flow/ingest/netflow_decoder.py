"""
NetFlow v5/v9 and IPFIX (v10) binary packet decoder.

decode() dispatches by version header.  Template state for v9/IPFIX is kept
inside NetFlowDecoder so out-of-order template packets still work — instantiate
one decoder per (exporter_ip, source_id) pair.
"""
from __future__ import annotations

import logging
import socket
import struct
from typing import Any

from .models import FlowRecord

logger = logging.getLogger(__name__)

# ── NetFlow v5 wire layout ────────────────────────────────────────────────────
_V5_HDR = struct.Struct("!HHIIIIHH")    # 24 bytes
_V5_REC = struct.Struct("!4s4s4sHHIIIIHHBBBBHHBBH")  # 48 bytes

# ── IPFIX / NetFlow v9 field-type IDs we care about ──────────────────────────
_F_BYTES        = 1
_F_PKTS         = 2
_F_PROTO        = 4
_F_TOS          = 5
_F_TCP_FLAGS    = 6
_F_SRC_PORT     = 7
_F_SRC_IP4      = 8
_F_IN_IF        = 10
_F_DST_PORT     = 11
_F_DST_IP4      = 12
_F_OUT_IF       = 14
_F_SRC_AS       = 16
_F_DST_AS       = 17
_F_END_UPTIME   = 21    # ms since boot
_F_START_UPTIME = 22    # ms since boot
_F_SRC_IP6      = 27
_F_DST_IP6      = 28
_F_START_SEC    = 150   # absolute seconds
_F_END_SEC      = 151
_F_START_MSEC   = 152   # absolute milliseconds
_F_END_MSEC     = 153

_IP4_FIELD_IDS = {_F_SRC_IP4, _F_DST_IP4, 15}


def _ip4(raw: bytes) -> str:
    return socket.inet_ntoa(raw)


def _ip4_int(n: int) -> str:
    return socket.inet_ntoa(struct.pack("!I", n))


class NetFlowDecoder:
    """Stateful per-exporter decoder.  Keeps v9/IPFIX template cache."""

    def __init__(self, exporter_ip: str) -> None:
        self._exporter_ip = exporter_ip
        # (source_id_or_obs_domain, template_id) → [(field_type, field_len), ...]
        self._templates: dict[tuple[int, int], list[tuple[int, int]]] = {}

    # ── public entry point ────────────────────────────────────────────────────

    def decode(self, data: bytes) -> list[FlowRecord]:
        if len(data) < 2:
            return []
        version = struct.unpack_from("!H", data, 0)[0]
        if version == 5:
            return self._v5(data)
        if version == 9:
            return self._v9(data)
        if version == 10:
            return self._ipfix(data)
        logger.debug("unknown NetFlow version %d from %s", version, self._exporter_ip)
        return []

    # ── NetFlow v5 ────────────────────────────────────────────────────────────

    def _v5(self, data: bytes) -> list[FlowRecord]:
        if len(data) < _V5_HDR.size:
            return []
        (_, count, sys_uptime_ms, unix_secs, unix_nsecs, _, _, _) = _V5_HDR.unpack_from(data)
        now = float(unix_secs) + float(unix_nsecs) / 1e9
        boot_epoch = now - sys_uptime_ms / 1000.0
        out: list[FlowRecord] = []
        for i in range(count):
            off = _V5_HDR.size + i * _V5_REC.size
            if off + _V5_REC.size > len(data):
                break
            (src_raw, dst_raw, _, in_if, out_if, d_pkts, d_oct,
             first_up, last_up, sp, dp, _, tcp_f, proto, tos,
             src_as, dst_as, _, _, _) = _V5_REC.unpack_from(data, off)
            abs_start = boot_epoch + first_up / 1000.0
            abs_end   = boot_epoch + last_up  / 1000.0
            out.append(FlowRecord(
                exporter_ip=self._exporter_ip,
                exporter_port=0,
                protocol_version="netflow5",
                src_ip=_ip4(src_raw),
                dst_ip=_ip4(dst_raw),
                src_port=sp,
                dst_port=dp,
                ip_protocol=proto,
                abs_start_time=abs_start,
                abs_end_time=abs_end,
                duration_ms=float(last_up - first_up),
                packets=d_pkts,
                bytes_count=d_oct,
                input_if=in_if,
                output_if=out_if,
                src_as=src_as,
                dst_as=dst_as,
                tcp_flags=tcp_f,
                tos=tos,
            ))
        return out

    # ── NetFlow v9 ────────────────────────────────────────────────────────────

    def _v9(self, data: bytes) -> list[FlowRecord]:
        hdr = struct.Struct("!HHIIII")
        if len(data) < hdr.size:
            return []
        _, _, sys_uptime_ms, unix_secs, _, source_id = hdr.unpack_from(data)
        boot_epoch = float(unix_secs) - sys_uptime_ms / 1000.0
        return self._parse_sets(data, hdr.size, source_id, boot_epoch, float(unix_secs), "netflow9")

    # ── IPFIX (v10) ───────────────────────────────────────────────────────────

    def _ipfix(self, data: bytes) -> list[FlowRecord]:
        hdr = struct.Struct("!HHIII")
        if len(data) < hdr.size:
            return []
        _, _, export_time, _, obs_domain_id = hdr.unpack_from(data)
        return self._parse_sets(data, hdr.size, obs_domain_id, 0.0, float(export_time), "ipfix")

    # ── shared set/template parsing ───────────────────────────────────────────

    def _parse_sets(
        self,
        data: bytes,
        pos: int,
        domain: int,
        boot_epoch: float,
        export_time: float,
        proto_ver: str,
    ) -> list[FlowRecord]:
        out: list[FlowRecord] = []
        while pos + 4 <= len(data):
            set_id, set_len = struct.unpack_from("!HH", data, pos)
            if set_len < 4 or pos + set_len > len(data):
                break
            payload = data[pos + 4: pos + set_len]
            pos += set_len
            if set_id in (0, 2):        # Template Set
                self._parse_templates(payload, domain, enterprise=False)
            elif set_id in (1, 3):      # Options Template — skip
                pass
            elif set_id >= 256:         # Data Set
                tmpl = self._templates.get((domain, set_id))
                if tmpl:
                    out.extend(self._decode_data(payload, tmpl, boot_epoch, export_time, proto_ver))
        return out

    def _parse_templates(self, data: bytes, domain: int, *, enterprise: bool) -> None:
        pos = 0
        while pos + 4 <= len(data):
            tmpl_id, field_count = struct.unpack_from("!HH", data, pos)
            pos += 4
            if tmpl_id < 256:
                break
            fields: list[tuple[int, int]] = []
            for _ in range(field_count):
                if pos + 4 > len(data):
                    break
                ftype, flen = struct.unpack_from("!HH", data, pos)
                pos += 4
                if enterprise and (ftype & 0x8000):     # enterprise bit
                    ftype &= 0x7FFF
                    pos += 4                             # skip enterprise number
                fields.append((ftype, flen))
            self._templates[(domain, tmpl_id)] = fields
            logger.debug("template domain=%d id=%d fields=%d", domain, tmpl_id, len(fields))

    def _decode_data(
        self,
        data: bytes,
        template: list[tuple[int, int]],
        boot_epoch: float,
        export_time: float,
        proto_ver: str,
    ) -> list[FlowRecord]:
        rec_len = sum(flen for _, flen in template)
        if rec_len == 0:
            return []
        out: list[FlowRecord] = []
        pos = 0
        while pos + rec_len <= len(data):
            f = self._extract(data, pos, template)
            pos += rec_len
            rec = self._to_record(f, boot_epoch, export_time, proto_ver)
            if rec:
                out.append(rec)
        return out

    def _extract(self, data: bytes, offset: int, template: list[tuple[int, int]]) -> dict[int, Any]:
        out: dict[int, Any] = {}
        pos = offset
        for ftype, flen in template:
            raw = data[pos: pos + flen]
            pos += flen
            if ftype in _IP4_FIELD_IDS and flen == 4:
                out[ftype] = _ip4(raw)
            elif flen <= 8:
                out[ftype] = int.from_bytes(raw, "big")
            else:
                out[ftype] = raw
        return out

    def _to_record(
        self,
        f: dict[int, Any],
        boot_epoch: float,
        export_time: float,
        proto_ver: str,
    ) -> FlowRecord | None:
        src_ip = f.get(_F_SRC_IP4)
        dst_ip = f.get(_F_DST_IP4)
        # IPv6 fallback — store as raw hex for now
        if src_ip is None and _F_SRC_IP6 in f:
            raw6 = f[_F_SRC_IP6]
            src_ip = socket.inet_ntop(socket.AF_INET6, raw6) if isinstance(raw6, (bytes, bytearray)) and len(raw6) == 16 else None
        if dst_ip is None and _F_DST_IP6 in f:
            raw6 = f[_F_DST_IP6]
            dst_ip = socket.inet_ntop(socket.AF_INET6, raw6) if isinstance(raw6, (bytes, bytearray)) and len(raw6) == 16 else None
        if not src_ip or not dst_ip:
            return None

        if _F_START_SEC in f and _F_END_SEC in f:
            abs_start = float(f[_F_START_SEC])
            abs_end   = float(f[_F_END_SEC])
        elif _F_START_MSEC in f and _F_END_MSEC in f:
            abs_start = float(f[_F_START_MSEC]) / 1000.0
            abs_end   = float(f[_F_END_MSEC])   / 1000.0
        elif _F_START_UPTIME in f and _F_END_UPTIME in f:
            abs_start = boot_epoch + f[_F_START_UPTIME] / 1000.0
            abs_end   = boot_epoch + f[_F_END_UPTIME]   / 1000.0
        else:
            abs_start = export_time
            abs_end   = export_time

        return FlowRecord(
            exporter_ip=self._exporter_ip,
            exporter_port=0,
            protocol_version=proto_ver,
            src_ip=str(src_ip),
            dst_ip=str(dst_ip),
            src_port=int(f.get(_F_SRC_PORT, 0)),
            dst_port=int(f.get(_F_DST_PORT, 0)),
            ip_protocol=int(f.get(_F_PROTO, 0)),
            abs_start_time=abs_start,
            abs_end_time=abs_end,
            duration_ms=max(0.0, (abs_end - abs_start) * 1000.0),
            packets=int(f.get(_F_PKTS, 0)),
            bytes_count=int(f.get(_F_BYTES, 0)),
            input_if=int(f.get(_F_IN_IF, 0)),
            output_if=int(f.get(_F_OUT_IF, 0)),
            src_as=int(f.get(_F_SRC_AS, 0)),
            dst_as=int(f.get(_F_DST_AS, 0)),
            tcp_flags=int(f.get(_F_TCP_FLAGS, 0)),
            tos=int(f.get(_F_TOS, 0)),
        )
