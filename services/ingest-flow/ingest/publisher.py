"""
NATS JetStream publisher (flow records + latency observations) and
InfluxDB writer (latency distributions).

Subjects published:
  netpulse.flows.<exporter_ip>.netflow5   — FlowRecord
  netpulse.flows.<exporter_ip>.netflow9   — FlowRecord
  netpulse.flows.<exporter_ip>.ipfix      — FlowRecord
  netpulse.flows.<exporter_ip>.sflow5     — FlowRecord
  netpulse.flows.<exporter_ip>.latency    — LatencyObservation
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import nats
import nats.js.errors

from .models import FlowRecord, LatencyObservation

logger = logging.getLogger(__name__)

_INVALID_TOKEN_RE = re.compile(r"[^a-zA-Z0-9.\-_]")


def _token(s: str) -> str:
    return _INVALID_TOKEN_RE.sub("_", s or "unknown") or "unknown"


class FlowPublisher:
    def __init__(
        self,
        nats_url: str,
        nats_user: str,
        nats_password: str,
        prefix: str,
        stream_name: str,
        stream_max_age_seconds: int,
        influxdb_url: str,
        influxdb_token: str,
        influxdb_org: str,
        influxdb_bucket: str,
    ) -> None:
        self._nats_url      = nats_url
        self._nats_user     = nats_user
        self._nats_password = nats_password
        self._prefix        = prefix
        self._stream_name   = stream_name
        self._max_age_ns    = stream_max_age_seconds * 1_000_000_000
        self._influx_url    = influxdb_url
        self._influx_token  = influxdb_token
        self._influx_org    = influxdb_org
        self._influx_bucket = influxdb_bucket

        self._nc: nats.NATS | None = None
        self._js = None
        self._influx_write_api = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._nc = await nats.connect(
            self._nats_url,
            user=self._nats_user,
            password=self._nats_password,
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream()
        logger.info("NATS connected: url=%s stream=%s", self._nats_url, self._stream_name)
        self._connect_influx()

    def _connect_influx(self) -> None:
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import ASYNCHRONOUS
            client = InfluxDBClient(
                url=self._influx_url,
                token=self._influx_token,
                org=self._influx_org,
            )
            self._influx_write_api = client.write_api(write_options=ASYNCHRONOUS)
            logger.info("InfluxDB connected: url=%s bucket=%s", self._influx_url, self._influx_bucket)
        except Exception as exc:
            logger.warning("InfluxDB connection failed (latency writes disabled): %s", exc)

    async def _ensure_stream(self) -> None:
        try:
            await self._js.stream_info(self._stream_name)
        except nats.js.errors.NotFoundError:
            await self._js.add_stream(
                name=self._stream_name,
                subjects=[f"{self._prefix}.>"],
            )
            logger.info("created JetStream stream %r", self._stream_name)

    async def drain(self) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            logger.info("NATS connection drained")

    # ── publish ────────────────────────────────────────────────────────────────

    async def publish_flow(self, record: FlowRecord) -> None:
        exporter = _token(record.exporter_ip)
        proto    = _token(record.protocol_version)
        subject  = f"{self._prefix}.{exporter}.{proto}"
        await self._pub(subject, record.to_dict())

    async def publish_latency(self, obs: LatencyObservation) -> None:
        src = _token(obs.src_device)
        subject = f"{self._prefix}.{src}.latency"
        await self._pub(subject, obs.to_dict())
        self._write_latency_influx(obs)

    async def _pub(self, subject: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), default=str).encode()
        try:
            ack = await self._js.publish(subject, data)
            logger.debug("published seq=%d to %s (%d bytes)", ack.seq, subject, len(data))
        except Exception as exc:
            logger.error("NATS publish failed for %s: %s", subject, exc)

    def _write_latency_influx(self, obs: LatencyObservation) -> None:
        if not self._influx_write_api:
            return
        try:
            from influxdb_client import Point
            point = (
                Point("transit_latency")
                .tag("src_device", obs.src_device)
                .tag("dst_device", obs.dst_device)
                .tag("ip_protocol", str(obs.ip_protocol))
                .field("latency_ms", obs.latency_ms)
                .time(obs.observed_at)
            )
            self._influx_write_api.write(bucket=self._influx_bucket, record=point)
        except Exception as exc:
            logger.error("InfluxDB write failed: %s", exc)
