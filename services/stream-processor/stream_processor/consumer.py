"""
NATS consumer: subscribe to all NetPulse telemetry subjects, fan out
to InfluxDB, OpenSearch, and alert republishing.

Subject map:
  netpulse.telemetry.*.metrics → InfluxDB measurement "telemetry"
  netpulse.telemetry.*.trap    → OpenSearch "netpulse-traps-YYYY.MM"
  netpulse.flows.*.netflow*    → OpenSearch "netpulse-flows-YYYY.MM" + flow anomaly
  netpulse.flows.*.sflow*      → OpenSearch "netpulse-flows-YYYY.MM" + flow anomaly
  netpulse.flows.*.latency     → InfluxDB "transit_latency" + latency anomaly
  netpulse.otel.*.metrics      → InfluxDB "otel_metrics"
  netpulse.otel.*.logs         → OpenSearch "netpulse-otel-logs-YYYY.MM" + log/auth anomaly
  netpulse.alerts.*            → (pass-through, external consumers handle persistence)
  netpulse.vendor.>            → OpenSearch "netpulse-vendor-YYYY.MM"
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time

from stream_processor import config
from stream_processor.alert_dedup import Alert, AlertDeduplicator
from stream_processor.anomaly.auth import AuthAnomalyDetector
from stream_processor.handlers import flows as flow_handler
from stream_processor.handlers import logs as log_handler
from stream_processor.handlers import metrics as metric_handler
from stream_processor.writers.influx import InfluxWriter
from stream_processor.writers.opensearch import OpenSearchWriter

logger = logging.getLogger(__name__)


async def run(
    nats_url: str = config.NATS_URL,
    nats_user: str = config.NATS_USER,
    nats_password: str = config.NATS_PASSWORD,
    influx: InfluxWriter | None = None,
    os_writer: OpenSearchWriter | None = None,
) -> None:
    import nats

    dedup     = AlertDeduplicator(cooldown_s=config.ALERT_COOLDOWN_S)
    auth_det  = AuthAnomalyDetector()

    nc = await nats.connect(nats_url, user=nats_user, password=nats_password)
    logger.info("NATS connected: %s", nats_url)

    async def _publish_alert(alert: Alert) -> None:
        if not dedup.should_publish(alert):
            return
        try:
            payload = json.dumps({
                "condition": alert.condition,
                "device_id": alert.device_id,
                "message":   alert.message,
                **alert.extra,
            }, default=str).encode()
            await nc.publish(f"netpulse.alerts.{alert.severity}", payload)
            logger.info("alert published: %s %s %s", alert.severity, alert.condition, alert.device_id)
        except Exception as exc:
            logger.error("alert publish failed: %s", exc)

    # ── telemetry metrics ─────────────────────────────────────────────────────

    async def on_telemetry_metrics(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        metric_handler.handle_telemetry_metrics(msg.subject, data, influx)

    # ── traps ─────────────────────────────────────────────────────────────────

    async def on_trap(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        await log_handler.handle_trap(msg.subject, data, os_writer)

    # ── flows ─────────────────────────────────────────────────────────────────

    async def on_flow(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        anomaly = await flow_handler.handle_flow(msg.subject, data, os_writer)
        if anomaly:
            await _publish_alert(Alert(
                severity="high", condition="high_flow_rate",
                device_id=anomaly.exporter_ip, message=anomaly.message,
                extra={"mbps": anomaly.mbps, "src_ip": anomaly.src_ip, "dst_ip": anomaly.dst_ip},
            ))

    async def on_latency(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        exceeded = flow_handler.handle_latency(msg.subject, data, influx)
        if exceeded:
            await _publish_alert(Alert(
                severity="medium", condition="high_latency",
                device_id=data.get("src_device", ""),
                message=(
                    f"Transit latency {data.get('latency_ms', 0):.1f}ms between "
                    f"{data.get('src_device')} and {data.get('dst_device')}"
                ),
                extra={"latency_ms": data.get("latency_ms", 0),
                       "dst_device": data.get("dst_device", "")},
            ))

    # ── OTEL metrics ──────────────────────────────────────────────────────────

    async def on_otel_metrics(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        metric_handler.handle_otel_metrics(msg.subject, data, influx)

    # ── OTEL logs ─────────────────────────────────────────────────────────────

    async def on_otel_logs(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        kw_hit, auth_hit = await log_handler.handle_otel_logs(
            msg.subject, data, os_writer, auth_det
        )
        if kw_hit:
            await _publish_alert(Alert(
                severity="low", condition="log_keyword",
                device_id=kw_hit.exporter_ip, message=kw_hit.message,
                extra={"keyword": kw_hit.matched_keyword},
            ))
        if auth_hit:
            await _publish_alert(Alert(
                severity=auth_hit.severity, condition=auth_hit.attack_type,
                device_id=auth_hit.device_ip, message=auth_hit.message,
                extra={"src_ip": auth_hit.src_ip, "count": auth_hit.count},
            ))

    # ── vendor events ─────────────────────────────────────────────────────────

    async def on_vendor(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        await log_handler.handle_vendor(msg.subject, data, os_writer)

    # ── subscriptions ─────────────────────────────────────────────────────────

    subs = [
        ("netpulse.telemetry.*.metrics", on_telemetry_metrics),
        ("netpulse.telemetry.*.trap",    on_trap),
        ("netpulse.flows.*.netflow5",    on_flow),
        ("netpulse.flows.*.netflow9",    on_flow),
        ("netpulse.flows.*.ipfix",       on_flow),
        ("netpulse.flows.*.sflow5",      on_flow),
        ("netpulse.flows.*.latency",     on_latency),
        ("netpulse.otel.*.metrics",      on_otel_metrics),
        ("netpulse.otel.*.logs",         on_otel_logs),
        ("netpulse.vendor.>",            on_vendor),
    ]
    for subject, handler in subs:
        await nc.subscribe(subject, cb=handler)
        logger.info("subscribed: %s", subject)

    # Periodic flush and auth-state eviction
    async def maintenance_loop() -> None:
        while True:
            await asyncio.sleep(config.BATCH_TIMEOUT)
            if os_writer:
                await os_writer.flush()
            auth_det.evict_stale()

    maintenance_task = asyncio.create_task(maintenance_loop())

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    logger.info("stream-processor running")
    await stop.wait()

    logger.info("shutdown: draining")
    maintenance_task.cancel()
    if os_writer:
        await os_writer.close()
    await nc.drain()
    logger.info("stream-processor stopped")
