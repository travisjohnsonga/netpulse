"""
Stream processor: NATS → InfluxDB / OpenSearch / PostgreSQL

Subscribes to all NetPulse telemetry subjects and fans data to storage:
  netpulse.telemetry.*.metrics → InfluxDB measurement "telemetry"
  netpulse.telemetry.*.trap    → OpenSearch index "netpulse-traps-YYYY.MM"
  netpulse.flows.*.netflow*    → OpenSearch index "netpulse-flows-YYYY.MM"
  netpulse.flows.*.sflow*      → OpenSearch index "netpulse-flows-YYYY.MM"
  netpulse.flows.*.latency     → InfluxDB measurement "transit_latency"
  netpulse.otel.*.metrics      → InfluxDB measurement "otel_metrics"
  netpulse.otel.*.logs         → OpenSearch index "netpulse-otel-logs-YYYY.MM"
  netpulse.alerts.*            → PostgreSQL AlertEvent
  netpulse.vendor.*            → OpenSearch index "netpulse-vendor-YYYY.MM"

Anomaly detection (simple threshold rules, in-process):
  - Flow: bytes > ANOMALY_FLOW_THRESHOLD_MBPS → netpulse.alerts.high
  - Latency: latency_ms > ANOMALY_LATENCY_THRESHOLD_MS → netpulse.alerts.medium
  - Log keyword match → netpulse.alerts.low
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# ── config from env ───────────────────────────────────────────────────────────
_NATS_URL      = os.environ.get("NATS_URL", "nats://nats:4222")
_NATS_USER     = os.environ.get("NATS_USER", "")
_NATS_PASS     = os.environ.get("NATS_PASSWORD", "")
_INFLUX_URL    = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
_INFLUX_TOKEN  = os.environ.get("INFLUXDB_ADMIN_TOKEN", "")
_INFLUX_ORG    = os.environ.get("INFLUXDB_ORG", "netpulse")
_INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "metrics")
_OS_URL        = os.environ.get("OPENSEARCH_URL", "http://opensearch:9200")
_OS_USER       = os.environ.get("OPENSEARCH_USER", "admin")
_OS_PASS       = os.environ.get("OPENSEARCH_PASSWORD", "")
_BATCH_SIZE    = int(os.environ.get("STREAM_PROCESSOR_BATCH_SIZE", "100"))
_BATCH_TIMEOUT = float(os.environ.get("STREAM_PROCESSOR_BATCH_TIMEOUT_SECONDS", "5"))
_FLOW_THRESHOLD_MBPS   = float(os.environ.get("ANOMALY_FLOW_THRESHOLD_MBPS", "1000"))
_LATENCY_THRESHOLD_MS  = float(os.environ.get("ANOMALY_LATENCY_THRESHOLD_MS", "500"))
_LOG_KEYWORDS = re.compile(r"\b(error|critical|down|unreachable|fail)\b", re.I)


def _index(prefix: str) -> str:
    month = datetime.now(timezone.utc).strftime("%Y.%m")
    return f"{prefix}-{month}"


def _daily_index(prefix: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    return f"{prefix}-{day}"


# Substrings indicating an authentication failure in a log line.
_AUTH_FAIL_KEYWORDS = (
    "authentication failure", "authentication failed", "failed password",
    "login failed", "auth fail", "invalid user", "access denied",
    "%sec_login-4", "%sec_login-5", "permission denied", "unauthorized",
)


def _log_body_text(data: dict) -> str:
    keys = ("message", "msg", "body", "syslog_msg", "log", "text", "description")
    return " ".join(str(data[k]) for k in keys if data.get(k)).lower()


class Command(BaseCommand):
    help = "Consume NATS telemetry and write to InfluxDB / OpenSearch / PostgreSQL"

    def handle(self, *args, **options):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        asyncio.run(_serve())


# ── alert dedup: suppress same (device, condition) within 5 minutes ──────────
_alert_last_seen: dict[tuple, float] = defaultdict(float)
_ALERT_COOLDOWN = 300.0


async def _serve() -> None:
    import nats
    import nats.js.errors

    loop = asyncio.get_running_loop()

    # ── InfluxDB ──────────────────────────────────────────────────────────────
    influx_write = None
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import ASYNCHRONOUS
        _influx_client = InfluxDBClient(url=_INFLUX_URL, token=_INFLUX_TOKEN, org=_INFLUX_ORG)
        influx_write = _influx_client.write_api(write_options=ASYNCHRONOUS)
        logger.info("InfluxDB connected: %s", _INFLUX_URL)
    except Exception as exc:
        logger.warning("InfluxDB unavailable (writes disabled): %s", exc)

    # ── OpenSearch ────────────────────────────────────────────────────────────
    os_client = None
    try:
        from opensearchpy import AsyncOpenSearch
        auth = (_OS_USER, _OS_PASS) if _OS_PASS else None
        os_client = AsyncOpenSearch(hosts=[_OS_URL], http_auth=auth,
                                     verify_certs=False, ssl_show_warn=False)
        logger.info("OpenSearch connected: %s", _OS_URL)
    except Exception as exc:
        logger.warning("OpenSearch unavailable (writes disabled): %s", exc)

    # ── NATS ──────────────────────────────────────────────────────────────────
    nc = await nats.connect(_NATS_URL, user=_NATS_USER, password=_NATS_PASS)
    js = nc.jetstream()
    logger.info("NATS connected: %s", _NATS_URL)

    # Bulk queue for OpenSearch
    os_bulk_queue: list[dict] = []
    last_flush = time.monotonic()

    async def _flush_os() -> None:
        nonlocal os_bulk_queue, last_flush
        if not os_client or not os_bulk_queue:
            os_bulk_queue = []
            last_flush = time.monotonic()
            return
        batch = os_bulk_queue[:]
        os_bulk_queue = []
        last_flush = time.monotonic()
        body: list = []
        for item in batch:
            body.append({"index": {"_index": item["_index"]}})
            body.append(item["doc"])
        try:
            await os_client.bulk(body=body)
            logger.debug("OpenSearch bulk flush: %d docs", len(batch))
        except Exception as exc:
            logger.error("OpenSearch bulk flush failed: %s", exc)

    async def _queue_os(index: str, doc: dict) -> None:
        nonlocal os_bulk_queue
        os_bulk_queue.append({"_index": index, "doc": doc})
        if len(os_bulk_queue) >= _BATCH_SIZE or (time.monotonic() - last_flush) >= _BATCH_TIMEOUT:
            await _flush_os()

    def _write_influx(measurement: str, tags: dict, fields: dict, ts=None) -> None:
        if not influx_write:
            return
        try:
            from influxdb_client import Point
            p = Point(measurement)
            for k, v in tags.items():
                p = p.tag(k, str(v))
            for k, v in fields.items():
                p = p.field(k, float(v) if isinstance(v, (int, float)) else str(v))
            if ts:
                p = p.time(ts)
            influx_write.write(bucket=_INFLUX_BUCKET, record=p)
        except Exception as exc:
            logger.error("InfluxDB write failed: %s", exc)

    async def _publish_alert(severity: str, payload: dict) -> None:
        key = (payload.get("device_id", ""), payload.get("condition", ""))
        now = time.monotonic()
        if now - _alert_last_seen[key] < _ALERT_COOLDOWN:
            return
        _alert_last_seen[key] = now
        try:
            data = json.dumps(payload, default=str).encode()
            await js.publish(f"netpulse.alerts.{severity}", data)
        except Exception as exc:
            logger.error("alert publish failed: %s", exc)

    # ── message handlers ──────────────────────────────────────────────────────

    async def on_telemetry_metrics(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        parts = msg.subject.split(".")
        device_id = parts[2] if len(parts) > 2 else "unknown"
        tags   = {"device_id": device_id}
        fields = {k: v for k, v in data.items()
                  if isinstance(v, (int, float)) and k not in ("abs_start_time", "abs_end_time")}
        if fields:
            _write_influx("telemetry", tags, fields)

    async def on_telemetry_trap(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        data["@timestamp"] = datetime.now(timezone.utc).isoformat()
        await _queue_os(_index("netpulse-traps"), data)

    async def on_flow(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        data["@timestamp"] = datetime.now(timezone.utc).isoformat()
        await _queue_os(_index("netpulse-flows"), data)
        # Anomaly: high byte rate
        bps = data.get("bytes", 0) / max(data.get("duration_ms", 1000) / 1000.0, 0.001)
        mbps = bps * 8 / 1_000_000
        if mbps > _FLOW_THRESHOLD_MBPS:
            await _publish_alert("high", {
                "condition": "high_flow_rate",
                "device_id": data.get("exporter_ip", ""),
                "message": f"Flow rate {mbps:.0f} Mbps from {data.get('src_ip')} to {data.get('dst_ip')}",
                "mbps": mbps,
            })

    async def on_latency(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        latency = data.get("latency_ms", 0)
        _write_influx("transit_latency",
                      {"src_device": data.get("src_device", ""), "dst_device": data.get("dst_device", "")},
                      {"latency_ms": latency})
        if latency > _LATENCY_THRESHOLD_MS:
            await _publish_alert("medium", {
                "condition": "high_latency",
                "device_id": data.get("src_device", ""),
                "message": f"Transit latency {latency:.1f}ms between {data.get('src_device')} and {data.get('dst_device')}",
                "latency_ms": latency,
            })

    async def on_otel_metrics(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        resource = data.get("resource_attrs", {})
        service  = resource.get("service.name", "unknown")
        for dp in data.get("data_points", []):
            val = dp.get("value", dp.get("sum", 0))
            if isinstance(val, (int, float)):
                _write_influx("otel_metrics",
                              {"service": service, "metric": data.get("metric_name", "")},
                              {"value": val})

    async def on_otel_logs(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        data["@timestamp"] = datetime.now(timezone.utc).isoformat()
        await _queue_os(_index("netpulse-otel-logs"), data)
        body = data.get("body", "")
        if _LOG_KEYWORDS.search(body):
            await _publish_alert("low", {
                "condition": "log_keyword",
                "device_id": data.get("exporter_ip", ""),
                "message": body[:200],
            })

    async def _publish_auth_event(source: str, data: dict) -> None:
        """Forward an auth-failure log to the security engine via netpulse.auth.events."""
        try:
            payload = {
                "source": source,
                "src_ip": data.get("src_ip") or data.get("host") or data.get("hostname", ""),
                "message": data.get("message") or data.get("msg") or data.get("body", ""),
                "severity": data.get("severity", ""),
                "raw": data,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            await nc.publish("netpulse.auth.events", json.dumps(payload, default=str).encode())
            logger.info("auth event published from %s", source)
        except Exception as exc:
            logger.error("auth event publish failed: %s", exc)

    async def on_log(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        parts = msg.subject.split(".")
        source = parts[2] if len(parts) > 2 else "unknown"
        data["@timestamp"] = datetime.now(timezone.utc).isoformat()
        data["source"] = source
        # Day-stamped logs index: netpulse-logs-YYYY.MM.DD
        await _queue_os(_daily_index("netpulse-logs"), data)

        body = _log_body_text(data)
        if not body:
            return
        # Auth failure → security engine
        if any(kw in body for kw in _AUTH_FAIL_KEYWORDS):
            await _publish_auth_event(source, data)
        # Anomaly keywords → flag for analysis (alerts)
        if _LOG_KEYWORDS.search(body):
            await _publish_alert("low", {
                "condition": "log_anomaly",
                "device_id": source,
                "message": body[:200],
            })

    async def on_alert(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        severity = msg.subject.split(".")[-1]
        # Persist to PostgreSQL via Django ORM
        try:
            from apps.alerts.models import AlertEvent, AlertRule
            await sync_to_async(_persist_alert)(severity, data)
        except Exception as exc:
            logger.error("alert persistence failed: %s", exc)

    def _persist_alert(severity: str, data: dict) -> None:
        from apps.alerts.models import AlertEvent, AlertRule
        rule, _ = AlertRule.objects.get_or_create(
            name=data.get("condition", "stream-processor"),
            defaults={"severity": severity, "condition": data, "is_active": True},
        )
        AlertEvent.objects.create(
            rule=rule,
            state=AlertEvent.State.FIRING,
            labels={"source": "stream-processor"},
            annotations=data,
        )

    async def on_vendor(msg) -> None:
        await msg.ack()
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        data["@timestamp"] = datetime.now(timezone.utc).isoformat()
        await _queue_os(_index("netpulse-vendor"), data)

    # ── subscriptions ─────────────────────────────────────────────────────────

    subscriptions = [
        ("netpulse.telemetry.*.metrics", on_telemetry_metrics),
        ("netpulse.telemetry.*.trap",    on_telemetry_trap),
        ("netpulse.flows.*.netflow5",    on_flow),
        ("netpulse.flows.*.netflow9",    on_flow),
        ("netpulse.flows.*.ipfix",       on_flow),
        ("netpulse.flows.*.sflow5",      on_flow),
        ("netpulse.flows.*.latency",     on_latency),
        ("netpulse.otel.*.metrics",      on_otel_metrics),
        ("netpulse.otel.*.logs",         on_otel_logs),
        ("netpulse.alerts.*",            on_alert),
        ("netpulse.vendor.>",            on_vendor),
    ]

    for subject, handler in subscriptions:
        await nc.subscribe(subject, cb=handler)
        logger.info("subscribed to %s", subject)

    # Logs flow through the JetStream LOGS stream — use a durable consumer so
    # buffered messages are replayed, falling back to core NATS if no stream.
    try:
        await js.subscribe("netpulse.logs.>", durable="stream-processor-logs",
                           stream="LOGS", cb=on_log)
        logger.info("subscribed (JetStream) to netpulse.logs.>")
    except Exception as exc:
        await nc.subscribe("netpulse.logs.>", cb=on_log)
        logger.info("subscribed (core NATS) to netpulse.logs.> — no stream (%s)", exc)

    # Periodic flush task
    async def flush_loop() -> None:
        while True:
            await asyncio.sleep(_BATCH_TIMEOUT)
            await _flush_os()

    flush_task = asyncio.create_task(flush_loop())

    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("stream-processor running")
    await stop_event.wait()

    logger.info("shutdown signal received")
    flush_task.cancel()
    await _flush_os()
    if os_client:
        await os_client.close()
    await nc.drain()
    logger.info("stream-processor stopped cleanly")
