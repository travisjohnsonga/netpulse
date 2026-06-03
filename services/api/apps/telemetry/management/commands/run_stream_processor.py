"""
stream-processor management command
====================================
Consumes NATS JetStream subjects and fans data out to:
  - InfluxDB  (metrics, latency)
  - OpenSearch (flows, logs, traps, OTEL logs)
  - PostgreSQL (alerts via Django ORM)

Usage:
  python manage.py run_stream_processor

Subject → storage mapping
--------------------------
  netpulse.telemetry.<device_id>.metrics  → InfluxDB "telemetry"
  netpulse.telemetry.<device_id>.trap     → OpenSearch "netpulse-traps-YYYY.MM"
  netpulse.flows.<exporter>.netflow5      → OpenSearch "netpulse-flows-YYYY.MM"
  netpulse.flows.<exporter>.latency       → InfluxDB "transit_latency"
  netpulse.otel.<service>.metrics         → InfluxDB "otel_metrics"
  netpulse.otel.<service>.logs            → OpenSearch "netpulse-otel-logs-YYYY.MM"
  netpulse.alerts.<severity>              → PostgreSQL AlertEvent
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _unit_status_text(status_ok, ok_text: str, bad_text: str) -> str:
    """Map a tri-state status_ok (True/False/None) to a stored status string.
    None → 'unknown' so a unit with no per-unit sensor is distinguishable from a
    healthy one when read back."""
    if status_ok is None:
        return "unknown"
    return ok_text if status_ok else bad_text


# ---------------------------------------------------------------------------
# Configuration (read once at startup from env / Django settings)
# ---------------------------------------------------------------------------

_BATCH_SIZE = int(os.environ.get("STREAM_PROCESSOR_BATCH_SIZE", "100"))
_BATCH_TIMEOUT = float(os.environ.get("STREAM_PROCESSOR_BATCH_TIMEOUT_SECONDS", "5"))
_FLOW_THRESHOLD_MBPS = float(os.environ.get("ANOMALY_FLOW_THRESHOLD_MBPS", "1000"))
_LATENCY_THRESHOLD_MS = float(os.environ.get("ANOMALY_LATENCY_THRESHOLD_MS", "500"))
_TEMP_WARNING_C = float(os.environ.get("TEMP_WARNING_C", "75"))
_TEMP_CRITICAL_C = float(os.environ.get("TEMP_CRITICAL_C", "85"))
_ALERT_COOLDOWN_SECS = 300  # 5 minutes per device/condition

# ---------------------------------------------------------------------------
# Token-bucket-style alert dedup: track last-fired time
# Key: (device_id_or_ip, condition_key)  →  epoch timestamp
# ---------------------------------------------------------------------------
_alert_last_fired: dict[tuple[str, str], float] = {}


def _can_fire_alert(entity: str, condition: str) -> bool:
    """Return True if the (entity, condition) alert hasn't fired within cooldown."""
    key = (entity, condition)
    now = time.monotonic()
    last = _alert_last_fired.get(key, 0.0)
    if now - last >= _ALERT_COOLDOWN_SECS:
        _alert_last_fired[key] = now
        return True
    return False


# ---------------------------------------------------------------------------
# Date-stamped index helper
# ---------------------------------------------------------------------------

def _index(prefix: str) -> str:
    """Return an index name like 'netpulse-flows-2025.06'."""
    stamp = datetime.now(timezone.utc).strftime("%Y.%m")
    return f"{prefix}-{stamp}"


def _daily_index(prefix: str) -> str:
    """Return a day-stamped index name like 'netpulse-logs-2025.06.14'."""
    stamp = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    return f"{prefix}-{stamp}"


# Substrings that indicate an authentication failure in a log line.
_AUTH_FAIL_KEYWORDS = (
    "authentication failure", "authentication failed", "failed password",
    "login failed", "auth fail", "invalid user", "access denied",
    "%sec_login-4", "%sec_login-5", "permission denied", "unauthorized",
)
# Substrings worth flagging for anomaly analysis.
_ANOMALY_KEYWORDS = ("error", "critical", "down", "unreachable", "denied", "fail")


def _log_body_text(payload: dict) -> str:
    """Concatenate the human-readable fields of a log payload, lowercased."""
    keys = ("message", "msg", "body", "syslog_msg", "log", "text", "description")
    return " ".join(str(payload[k]) for k in keys if payload.get(k)).lower()


# ---------------------------------------------------------------------------
# Main Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = "Consume NATS telemetry and write to InfluxDB / OpenSearch / PostgreSQL"

    def handle(self, *args, **options):
        asyncio.run(self._serve())

    # -----------------------------------------------------------------------
    # Top-level coroutine
    # -----------------------------------------------------------------------

    async def _serve(self):
        import nats
        from django.conf import settings

        logger.info("stream-processor: starting")

        # --- shared stop event -----------------------------------------------
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _shutdown(signum, _frame):
            logger.info("stream-processor: received signal %s, shutting down", signum)
            loop.call_soon_threadsafe(stop_event.set)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        # --- connect storage backends ----------------------------------------
        influx_client, influx_write_api = self._connect_influx(settings)
        os_client = self._connect_opensearch(settings)

        # We keep a reference to nc so _check_anomalies can publish to NATS.
        # It is set after the NATS connection is established below.
        self._nc = None
        self._influx_write_api = influx_write_api
        self._os_client = os_client
        self._influx_bucket = settings.INFLUXDB_BUCKET
        self._influx_org = settings.INFLUXDB_ORG

        # --- OpenSearch bulk buffer -------------------------------------------
        # buffer: list of (index, doc) tuples
        self._os_buffer: list[tuple[str, dict]] = []
        self._os_buffer_lock = asyncio.Lock()

        # Per-interface counter state for rate calculation:
        # (device_id, if_index) → {counter_key: value, "_t": epoch_seconds}
        self._iface_prev: dict = {}

        # --- connect NATS -------------------------------------------------------
        nc_opts: dict = {
            "servers": settings.NATS_URL,
        }
        if settings.NATS_USER:
            nc_opts["user"] = settings.NATS_USER
        if settings.NATS_PASSWORD:
            nc_opts["password"] = settings.NATS_PASSWORD

        nc = await nats.connect(**nc_opts)
        self._nc = nc
        logger.info("stream-processor: connected to NATS at %s", settings.NATS_URL)

        # --- create subscriptions -----------------------------------------------
        js = nc.jetstream()

        # We use push-based consumers with JetStream where streams exist,
        # and fall back to core NATS subscribe for subjects that have no stream yet.
        subscriptions = []

        # JetStream durable consumers
        js_subjects = [
            ("netpulse.telemetry.>", "TELEMETRY", self._on_telemetry),
            ("netpulse.flows.>", "FLOWS", self._on_flow),
            ("netpulse.otel.>", "OTEL", self._on_otel),
            ("netpulse.logs.>", "LOGS", self._on_log),
            ("netpulse.alerts.>", "ALERTS", self._on_alert),
        ]

        for subject, stream_name, cb in js_subjects:
            sub = await self._js_subscribe(js, subject, stream_name, cb)
            if sub is not None:
                subscriptions.append(sub)
                logger.debug("stream-processor: JetStream subscribed to %s", subject)
            else:
                # Fall back to core NATS subscribe
                sub = await nc.subscribe(subject, cb=cb)
                subscriptions.append(sub)
                logger.debug(
                    "stream-processor: core-NATS subscribed to %s (no stream)", subject
                )

        # --- background flush task --------------------------------------------
        flush_task = asyncio.create_task(self._os_flush_loop(stop_event))
        # --- liveness heartbeat for run_health_checks -------------------------
        hb_task = asyncio.create_task(self._heartbeat_loop(stop_event))

        logger.info("stream-processor: ready — consuming messages")

        # --- wait for shutdown ------------------------------------------------
        await stop_event.wait()
        logger.info("stream-processor: draining…")

        # cancel flush + heartbeat loops and do a final flush
        flush_task.cancel()
        hb_task.cancel()
        for t in (flush_task, hb_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await self._os_flush_now()

        # flush InfluxDB
        try:
            if influx_write_api is not None:
                influx_write_api.close()
                logger.debug("stream-processor: InfluxDB write API closed")
        except Exception as exc:
            logger.warning("stream-processor: error closing InfluxDB write API: %s", exc)

        # drain NATS
        try:
            await nc.drain()
        except Exception as exc:
            logger.warning("stream-processor: error draining NATS: %s", exc)

        # close OpenSearch
        try:
            if os_client is not None:
                await os_client.close()
                logger.debug("stream-processor: OpenSearch client closed")
        except Exception as exc:
            logger.warning("stream-processor: error closing OpenSearch client: %s", exc)

        logger.info("stream-processor stopped cleanly")

    # -----------------------------------------------------------------------
    # Storage backend constructors
    # -----------------------------------------------------------------------

    def _connect_influx(self, settings):
        """Return (InfluxDBClient, WriteApi) or (None, None) on error."""
        try:
            from influxdb_client import InfluxDBClient, WriteOptions
            from influxdb_client.client.write_api import ASYNCHRONOUS

            client = InfluxDBClient(
                url=settings.INFLUXDB_URL,
                token=settings.INFLUXDB_TOKEN,
                org=settings.INFLUXDB_ORG,
            )
            write_api = client.write_api(write_options=ASYNCHRONOUS)
            logger.info("stream-processor: InfluxDB client ready (%s)", settings.INFLUXDB_URL)
            return client, write_api
        except Exception as exc:
            logger.error("stream-processor: failed to connect to InfluxDB: %s", exc)
            return None, None

    def _connect_opensearch(self, settings):
        """Return async OpenSearch client or None on error."""
        try:
            from opensearchpy import AsyncOpenSearch

            no_auth = os.environ.get("OPENSEARCH_NO_AUTH", "").lower() in ("1", "true", "yes")
            host = settings.OPENSEARCH_HOST
            port = settings.OPENSEARCH_PORT
            use_ssl = settings.OPENSEARCH_USE_SSL

            kwargs: dict = {
                "hosts": [{"host": host, "port": port}],
                "use_ssl": use_ssl,
                "verify_certs": use_ssl,
            }
            if not no_auth:
                kwargs["http_auth"] = (settings.OPENSEARCH_USER, settings.OPENSEARCH_PASSWORD)

            client = AsyncOpenSearch(**kwargs)
            logger.info("stream-processor: OpenSearch client ready (%s:%s)", host, port)
            return client
        except Exception as exc:
            logger.error("stream-processor: failed to create OpenSearch client: %s", exc)
            return None

    # -----------------------------------------------------------------------
    # JetStream consumer helper
    # -----------------------------------------------------------------------

    async def _js_subscribe(self, js, subject: str, stream_name: str, cb):
        """
        Attempt a durable push-consumer subscription on the given stream.
        Returns the subscription or None if the stream does not exist.
        """
        try:
            consumer_name = f"sp-{stream_name.lower()}"
            sub = await js.subscribe(
                subject,
                durable=consumer_name,
                stream=stream_name,
                cb=cb,
                manual_ack=False,
            )
            return sub
        except Exception as exc:
            logger.warning(
                "stream-processor: JetStream subscribe failed for %s/%s: %s",
                stream_name,
                subject,
                exc,
            )
            return None

    # -----------------------------------------------------------------------
    # NATS message handlers
    # -----------------------------------------------------------------------

    async def _on_telemetry(self, msg):
        """Handle netpulse.telemetry.<device_id>.<type> messages."""
        try:
            parts = msg.subject.split(".")
            # parts: ["netpulse", "telemetry", <device_id>, <type>]
            device_id = parts[2] if len(parts) > 2 else "unknown"
            msg_type = parts[3] if len(parts) > 3 else "metrics"

            payload = json.loads(msg.data)
            logger.debug(
                "stream-processor: telemetry msg subject=%s bytes=%d",
                msg.subject,
                len(msg.data),
            )

            if msg_type == "metrics":
                fields = self._extract_fields(payload)
                ts = payload.get("timestamp")
                # Derive environment metrics (CPU avg / memory % / temperature /
                # fan+PSU counts) from the GET + table-walk results. Scalars are
                # merged into the telemetry point; per-sensor temps get their own
                # device_environment measurement.
                env = self._derive_environment(payload)
                fields.update(env["scalars"])
                await self._write_influx(
                    measurement="telemetry",
                    tags={"device_id": device_id, "protocol": payload.get("protocol", "unknown")},
                    fields=fields,
                    timestamp=ts,
                )
                await self._write_environment(device_id, env, ts)
                await self._check_temperature(device_id, payload.get("hostname"), env)
                # Derive per-interface bps/pps/error/util rates from the counters.
                await self._interface_stats(device_id, fields, ts)
            elif msg_type == "trap":
                doc = {"device_id": device_id, **payload, "@timestamp": _utcnow_iso()}
                await self._buffer_opensearch(_index("netpulse-traps"), doc)
                # Traps are always noteworthy — treat as low-priority log anomaly check
                await self._check_anomalies("log", device_id, payload)

        except Exception as exc:
            logger.error(
                "stream-processor: error processing telemetry msg %s: %s", msg.subject, exc
            )

    # Interface counter field (resolved name or raw OID base) → (metric, is_counter, max).
    _IFACE_COUNTERS = {
        "ifHCInOctets": ("in_octets", 2 ** 64), "ifHCOutOctets": ("out_octets", 2 ** 64),
        "ifHCInUcastPkts": ("in_pkts", 2 ** 64), "ifHCOutUcastPkts": ("out_pkts", 2 ** 64),
        "ifInErrors": ("in_errors", 2 ** 32), "ifOutErrors": ("out_errors", 2 ** 32),
        "ifInDiscards": ("in_discards", 2 ** 32), "ifOutDiscards": ("out_discards", 2 ** 32),
        "ifInUnknownProtos": ("in_unknown", 2 ** 32),
        # Raw OID bases (when the poller couldn't resolve a name).
        "1_3_6_1_2_1_31_1_1_1_6": ("in_octets", 2 ** 64), "1_3_6_1_2_1_31_1_1_1_10": ("out_octets", 2 ** 64),
        "1_3_6_1_2_1_31_1_1_1_7": ("in_pkts", 2 ** 64), "1_3_6_1_2_1_31_1_1_1_11": ("out_pkts", 2 ** 64),
        "1_3_6_1_2_1_2_2_1_14": ("in_errors", 2 ** 32), "1_3_6_1_2_1_2_2_1_20": ("out_errors", 2 ** 32),
        "1_3_6_1_2_1_2_2_1_13": ("in_discards", 2 ** 32), "1_3_6_1_2_1_2_2_1_19": ("out_discards", 2 ** 32),
        "1_3_6_1_2_1_2_2_1_15": ("in_unknown", 2 ** 32),
    }
    _IFACE_GAUGES = {
        "ifHighSpeed": "high_speed", "1_3_6_1_2_1_31_1_1_1_15": "high_speed",
        "ifOperStatus": "oper_status", "1_3_6_1_2_1_2_2_1_8": "oper_status",
    }

    # gNMI/MDT interface counters arrive keyed "<InterfaceName>/<leaf>" (e.g.
    # "GigabitEthernet0/0/0/in_octets"). OpenConfig/IOS-XE counters are all
    # uint64, so a single 2**64 rollover ceiling is correct for every leaf.
    _GNMI_COUNTERS = {
        "in_octets": "in_octets", "out_octets": "out_octets",
        "in_pkts": "in_pkts", "out_pkts": "out_pkts",
        "in_unicast_pkts": "in_pkts", "out_unicast_pkts": "out_pkts",
        "in_errors": "in_errors", "out_errors": "out_errors",
        "in_discards": "in_discards", "out_discards": "out_discards",
    }
    _GNMI_GAUGES = {
        # speed (IOS-XE reports Mbps as a number) feeds utilisation; oper-status
        # is usually a string enum and is skipped as non-numeric upstream.
        "speed": "high_speed",
    }

    @staticmethod
    def _counter_delta(cur, prev, maxv):
        """Counter delta with rollover handling."""
        if cur >= prev:
            return cur - prev
        return maxv - prev + cur

    async def _interface_stats(self, device_id, fields: dict, timestamp):
        """
        Derive per-interface bps/pps/error/discard rates and utilisation from
        the raw counters in a telemetry message, and write them to the
        ``interface_stats`` measurement (tags device_id+if_index).
        """
        # Two on-the-wire shapes feed this:
        #   SNMP  → "<base>_<ifindex>"          e.g. "ifHCInOctets_2"
        #   gNMI  → "<InterfaceName>/<leaf>"     e.g. "GigabitEthernet0/0/0/in_octets"
        # Both collapse to one per-interface bucket keyed by a stable id (the
        # ifIndex for SNMP, the interface name for gNMI) used as the if_index tag.
        per_iface: dict = {}
        maxv: dict = {}
        for key, val in fields.items():
            if not isinstance(val, (int, float)):
                continue
            if "/" in key:
                # gNMI: split on the LAST slash — interface names contain slashes.
                name, _, leaf = key.rpartition("/")
                if not name:
                    continue
                if leaf in self._GNMI_COUNTERS:
                    metric = self._GNMI_COUNTERS[leaf]
                    per_iface.setdefault(name, {})[metric] = val
                    maxv[metric] = 2 ** 64
                elif leaf in self._GNMI_GAUGES:
                    per_iface.setdefault(name, {})[self._GNMI_GAUGES[leaf]] = val
                continue
            base, _, idx = key.rpartition("_")
            if not idx.isdigit():
                continue
            if base in self._IFACE_COUNTERS:
                metric, mx = self._IFACE_COUNTERS[base]
                per_iface.setdefault(idx, {})[metric] = val
                maxv[metric] = mx
            elif base in self._IFACE_GAUGES:
                per_iface.setdefault(idx, {})[self._IFACE_GAUGES[base]] = val
        if not per_iface:
            return

        # Resolve the sample time (epoch seconds) for the rate denominator.
        now_epoch = None
        if timestamp:
            try:
                now_epoch = datetime.fromisoformat(timestamp).timestamp()
            except (ValueError, TypeError):
                now_epoch = None
        if now_epoch is None:
            now_epoch = datetime.now(timezone.utc).timestamp()

        for idx, cur in per_iface.items():
            state_key = (device_id, idx)
            prev = self._iface_prev.get(state_key)
            high_speed = cur.get("high_speed")  # Mbps
            out_fields: dict = {}
            if "oper_status" in cur:
                out_fields["oper_status"] = int(cur["oper_status"])

            if prev:
                dt = now_epoch - prev.get("_t", now_epoch)
                if dt > 0:
                    rate = {}
                    for metric in ("in_octets", "out_octets", "in_pkts", "out_pkts",
                                   "in_errors", "out_errors", "in_discards", "out_discards"):
                        if metric in cur and metric in prev:
                            d = self._counter_delta(cur[metric], prev[metric], maxv.get(metric, 2 ** 64))
                            rate[metric] = d / dt
                    out_fields["in_bps"] = round(rate.get("in_octets", 0.0) * 8, 2)
                    out_fields["out_bps"] = round(rate.get("out_octets", 0.0) * 8, 2)
                    out_fields["in_pps"] = round(rate.get("in_pkts", 0.0), 2)
                    out_fields["out_pps"] = round(rate.get("out_pkts", 0.0), 2)
                    out_fields["in_errors_rate"] = round(rate.get("in_errors", 0.0), 4)
                    out_fields["out_errors_rate"] = round(rate.get("out_errors", 0.0), 4)
                    out_fields["in_discards_rate"] = round(rate.get("in_discards", 0.0), 4)
                    out_fields["out_discards_rate"] = round(rate.get("out_discards", 0.0), 4)
                    if isinstance(high_speed, (int, float)) and high_speed > 0:
                        cap = high_speed * 1_000_000
                        out_fields["in_util_pct"] = round(out_fields["in_bps"] / cap * 100, 3)
                        out_fields["out_util_pct"] = round(out_fields["out_bps"] / cap * 100, 3)

            # Persist current counters + time for the next delta.
            new_state = {"_t": now_epoch}
            for metric in ("in_octets", "out_octets", "in_pkts", "out_pkts",
                           "in_errors", "out_errors", "in_discards", "out_discards"):
                if metric in cur:
                    new_state[metric] = cur[metric]
            self._iface_prev[state_key] = new_state

            if out_fields:
                # For gNMI the bucket key IS the interface name (parsed from the
                # part before "/" in e.g. "GigabitEthernet1/rx_kbps"), so also
                # write it as an if_name tag. SNMP buckets are numeric ifIndex
                # and carry no name here (the reader resolves it).
                tags = {"device_id": device_id, "if_index": idx}
                if not str(idx).isdigit():
                    tags["if_name"] = idx
                await self._write_influx(
                    measurement="interface_stats",
                    tags=tags,
                    fields=out_fields,
                    timestamp=timestamp,
                )

    async def _on_flow(self, msg):
        """Handle netpulse.flows.<exporter>.<type> messages."""
        try:
            parts = msg.subject.split(".")
            exporter_ip = parts[2] if len(parts) > 2 else "unknown"
            flow_type = parts[3] if len(parts) > 3 else "netflow5"

            payload = json.loads(msg.data)
            logger.debug(
                "stream-processor: flow msg subject=%s bytes=%d",
                msg.subject,
                len(msg.data),
            )

            if flow_type == "latency":
                # LatencyObservation dict
                latency_ms = float(payload.get("latency_ms", 0))
                await self._write_influx(
                    measurement="transit_latency",
                    tags={
                        "src_device": payload.get("src_device", exporter_ip),
                        "dst_device": payload.get("dst_device", "unknown"),
                        "ip_protocol": str(payload.get("ip_protocol", 0)),
                    },
                    fields={"latency_ms": latency_ms},
                    timestamp=payload.get("observed_at"),
                )
                await self._check_anomalies("latency", exporter_ip, payload)
            else:
                # FlowRecord dict — write to OpenSearch
                doc = {
                    "exporter_ip": exporter_ip,
                    "protocol_version": flow_type,
                    "@timestamp": _utcnow_iso(),
                    **payload,
                }
                await self._buffer_opensearch(_index("netpulse-flows"), doc)

                # Anomaly check: bytes per second
                duration_ms = float(payload.get("duration_ms", 1)) or 1.0
                bytes_count = int(payload.get("bytes", payload.get("bytes_count", 0)))
                bps = (bytes_count / (duration_ms / 1000)) * 8  # bits per second
                mbps = bps / 1_000_000
                if mbps > _FLOW_THRESHOLD_MBPS:
                    await self._check_anomalies("flow_threshold", exporter_ip, {
                        "mbps": mbps,
                        **payload,
                    })

        except Exception as exc:
            logger.error(
                "stream-processor: error processing flow msg %s: %s", msg.subject, exc
            )

    async def _on_otel(self, msg):
        """Handle netpulse.otel.<service>.<type> messages."""
        try:
            parts = msg.subject.split(".")
            service_name = parts[2] if len(parts) > 2 else "unknown"
            otel_type = parts[3] if len(parts) > 3 else "metrics"

            payload = json.loads(msg.data)
            logger.debug(
                "stream-processor: otel msg subject=%s bytes=%d",
                msg.subject,
                len(msg.data),
            )

            if otel_type == "metrics":
                await self._write_influx(
                    measurement="otel_metrics",
                    tags={"service": service_name},
                    fields=self._extract_fields(payload),
                    timestamp=payload.get("timestamp"),
                )
            elif otel_type == "logs":
                doc = {"service": service_name, "@timestamp": _utcnow_iso(), **payload}
                await self._buffer_opensearch(_index("netpulse-otel-logs"), doc)
                await self._check_anomalies("log", service_name, payload)

        except Exception as exc:
            logger.error(
                "stream-processor: error processing otel msg %s: %s", msg.subject, exc
            )

    async def _on_alert(self, msg):
        """Handle netpulse.alerts.<severity> messages → PostgreSQL AlertEvent."""
        try:
            parts = msg.subject.split(".")
            severity = parts[2] if len(parts) > 2 else "info"

            payload = json.loads(msg.data)
            logger.debug(
                "stream-processor: alert msg subject=%s bytes=%d",
                msg.subject,
                len(msg.data),
            )

            await self._write_alert(severity, payload)

        except Exception as exc:
            logger.error(
                "stream-processor: error processing alert msg %s: %s", msg.subject, exc
            )

    async def _on_log(self, msg):
        """
        Handle netpulse.logs.<source>... messages.

        - Write the log line to OpenSearch index netpulse-logs-YYYY.MM.DD.
        - On auth-failure keywords: publish to netpulse.auth.events (security engine).
        - On anomaly keywords: flag via the shared anomaly path (alerts).
        """
        try:
            parts = msg.subject.split(".")
            # parts: ["netpulse", "logs", <source/device_id>, ...]
            source = parts[2] if len(parts) > 2 else "unknown"

            payload = json.loads(msg.data)
            logger.debug(
                "stream-processor: log msg subject=%s bytes=%d", msg.subject, len(msg.data)
            )

            doc = {"source": source, "subject": msg.subject, "@timestamp": _utcnow_iso(), **payload}
            await self._buffer_opensearch(_daily_index("netpulse-logs"), doc)

            await self._inspect_log_security(source, payload)

        except Exception as exc:
            logger.error(
                "stream-processor: error processing log msg %s: %s", msg.subject, exc
            )

    async def _inspect_log_security(self, source: str, payload: dict):
        """Scan a log payload for auth failures and anomaly keywords."""
        body = _log_body_text(payload)
        if not body:
            return

        # Auth failure → security engine via netpulse.auth.events
        if any(kw in body for kw in _AUTH_FAIL_KEYWORDS) and _can_fire_alert(source, "auth_fail"):
            await self._publish_auth_event({
                "source": source,
                "src_ip": payload.get("src_ip") or payload.get("host") or payload.get("hostname", ""),
                "message": payload.get("message") or payload.get("msg") or payload.get("body", ""),
                "severity": payload.get("severity", ""),
                "raw": payload,
                "detected_at": _utcnow_iso(),
            })

        # Anomaly keywords → flag for analysis (shared anomaly path → alerts)
        if any(kw in body for kw in _ANOMALY_KEYWORDS):
            await self._check_anomalies("log", source, payload)

    async def _publish_auth_event(self, payload: dict):
        """Publish an auth-failure event to netpulse.auth.events for the security engine."""
        if self._nc is None:
            logger.warning("stream-processor: cannot publish auth event — NATS not connected")
            return
        try:
            await self._nc.publish("netpulse.auth.events", json.dumps(payload, default=str).encode())
            logger.info("stream-processor: published auth event from %s", payload.get("source"))
        except Exception as exc:
            logger.error("stream-processor: failed to publish auth event: %s", exc)

    # -----------------------------------------------------------------------
    # InfluxDB writer
    # -----------------------------------------------------------------------

    async def _write_influx(
        self,
        measurement: str,
        tags: dict,
        fields: dict,
        timestamp=None,
    ):
        """Write a single data point to InfluxDB (non-blocking)."""
        if self._influx_write_api is None:
            return
        if not fields:
            return

        try:
            from influxdb_client import Point

            p = Point(measurement)
            for k, v in tags.items():
                if v is not None:
                    p = p.tag(k, str(v))
            for k, v in fields.items():
                if v is not None:
                    try:
                        p = p.field(k, float(v))
                    except (TypeError, ValueError):
                        p = p.field(k, str(v))
            if timestamp:
                if isinstance(timestamp, str):
                    # ISO 8601 string
                    try:
                        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        p = p.time(ts)
                    except ValueError:
                        pass
                elif isinstance(timestamp, (int, float)):
                    p = p.time(int(timestamp), write_precision="s")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self._influx_write_api.write(
                    bucket=self._influx_bucket, org=self._influx_org, record=p
                ),
            )
            logger.debug(
                "stream-processor: influx write measurement=%s tags=%s", measurement, tags
            )
        except Exception as exc:
            logger.error("stream-processor: InfluxDB write error: %s", exc)

    # -----------------------------------------------------------------------
    # OpenSearch bulk writer
    # -----------------------------------------------------------------------

    async def _buffer_opensearch(self, index: str, doc: dict):
        """Add a document to the bulk buffer; flush if batch size is reached."""
        async with self._os_buffer_lock:
            self._os_buffer.append((index, doc))
            if len(self._os_buffer) >= _BATCH_SIZE:
                await self._flush_locked()

    async def _heartbeat_loop(self, stop_event: asyncio.Event):
        """Write service:heartbeat:stream-processor to Valkey every 60s (TTL 300s)
        so run_health_checks can see this service is alive."""
        from urllib.parse import quote
        url = os.environ.get("VALKEY_URL")
        if not url:
            pw = os.environ.get("VALKEY_PASSWORD", "")
            auth = f":{quote(pw, safe='')}@" if pw else ""
            url = f"redis://{auth}{os.environ.get('VALKEY_HOST', 'valkey')}:{os.environ.get('VALKEY_PORT', '6379')}/0"
        try:
            import redis.asyncio as redis
        except Exception as exc:
            logger.warning("stream-processor: heartbeat disabled (%s)", exc)
            return
        client = redis.from_url(url)
        try:
            while not stop_event.is_set():
                try:
                    await client.set("service:heartbeat:stream-processor",
                                     datetime.now(timezone.utc).isoformat(), ex=300)
                except Exception as exc:
                    logger.debug("stream-processor: heartbeat write failed: %s", exc)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
        finally:
            try:
                await client.aclose()
            except Exception:
                pass

    async def _os_flush_loop(self, stop_event: asyncio.Event):
        """Background task: flush OpenSearch buffer every BATCH_TIMEOUT seconds."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_BATCH_TIMEOUT)
            except asyncio.TimeoutError:
                pass
            await self._os_flush_now()

    async def _os_flush_now(self):
        """Flush the OpenSearch bulk buffer (thread-safe)."""
        async with self._os_buffer_lock:
            await self._flush_locked()

    async def _flush_locked(self):
        """Internal flush — must be called with _os_buffer_lock held."""
        if not self._os_buffer or self._os_client is None:
            return

        batch = self._os_buffer[:]
        self._os_buffer.clear()

        # Build bulk body
        bulk_body: list[dict] = []
        for index, doc in batch:
            bulk_body.append({"index": {"_index": index}})
            bulk_body.append(doc)

        try:
            response = await self._os_client.bulk(body=bulk_body)
            errors = response.get("errors", False)
            if errors:
                logger.warning(
                    "stream-processor: OpenSearch bulk had errors (first item): %s",
                    response.get("items", [{}])[0],
                )
            else:
                logger.debug(
                    "stream-processor: OpenSearch bulk flushed %d docs", len(batch)
                )
        except Exception as exc:
            logger.error("stream-processor: OpenSearch bulk flush error: %s", exc)

    # -----------------------------------------------------------------------
    # Alert writer (PostgreSQL via Django ORM)
    # -----------------------------------------------------------------------

    async def _write_alert(self, severity: str, payload: dict):
        """
        Persist an AlertEvent to PostgreSQL.

        We look for an existing open (FIRING) AlertRule with a matching name
        or create a synthetic one so we can attach the AlertEvent.

        All DB work runs in a thread executor to avoid blocking the event loop.
        """
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._db_write_alert, severity, payload)
        except Exception as exc:
            logger.error("stream-processor: DB alert write error: %s", exc)

    @staticmethod
    def _db_write_alert(severity: str, payload: dict):
        """Synchronous DB write — runs in thread executor."""
        from apps.alerts.models import AlertEvent, AlertRule

        rule_name = payload.get("rule_name", f"stream-processor-{severity}")
        description = payload.get("description", "Auto-generated by stream-processor")
        # Merge identifying top-level payload fields into labels so downstream
        # auto-resolution can match by source/device_id/check_id (the reachability
        # and check-engine publishers put these at the payload top level).
        labels = dict(payload.get("labels", {}))
        for key in ("source", "device_id", "hostname", "check_id", "site_id", "interface", "if_name"):
            if payload.get(key) is not None and key not in labels:
                labels[key] = payload[key]
        annotations = dict(payload.get("annotations", {}))
        for key in ("title", "message"):
            if payload.get(key) is not None and key not in annotations:
                annotations[key] = payload[key]

        # Map severity string to AlertRule.Severity choices
        sev_map = {
            "critical": AlertRule.Severity.CRITICAL,
            "high": AlertRule.Severity.HIGH,
            "medium": AlertRule.Severity.MEDIUM,
            "low": AlertRule.Severity.LOW,
            "info": AlertRule.Severity.INFO,
        }
        sev_choice = sev_map.get(severity.lower(), AlertRule.Severity.INFO)

        # get_or_create the AlertRule
        rule, created = AlertRule.objects.get_or_create(
            name=rule_name,
            defaults={
                "description": description,
                "severity": sev_choice,
                "condition": {"source": "stream-processor"},
                "is_active": True,
            },
        )
        if created:
            logger.debug("stream-processor: created AlertRule %r", rule_name)

        # A disabled rule suppresses its alerts (UI toggle on Settings →
        # Alerting). Newly auto-created rules default to active, so this only
        # skips rules an operator has explicitly turned off.
        if not rule.is_active:
            logger.info("stream-processor: rule %r disabled, suppressing alert", rule_name)
            return

        # Always create a new AlertEvent
        event = AlertEvent.objects.create(
            rule=rule,
            state=AlertEvent.State.FIRING,
            labels=labels,
            annotations=annotations,
        )
        logger.info(
            "stream-processor: AlertEvent pk=%s rule=%r severity=%s",
            event.pk,
            rule_name,
            severity,
        )

    # -----------------------------------------------------------------------
    # Anomaly detection
    # -----------------------------------------------------------------------

    async def _check_anomalies(self, record_type: str, entity: str, data: dict):
        """
        Simple in-process, rules-based anomaly detection.

        record_type: "flow_threshold" | "latency" | "log" | "trap"
        entity:      device_id, exporter_ip, or service name
        data:        the parsed payload dict

        Fires an alert by publishing to netpulse.alerts.<severity> if:
          - flow_threshold: mbps > ANOMALY_FLOW_THRESHOLD_MBPS
          - latency:        latency_ms > ANOMALY_LATENCY_THRESHOLD_MS
          - log/trap:       body contains error keywords
        """
        if record_type == "flow_threshold":
            mbps = float(data.get("mbps", 0))
            if mbps > _FLOW_THRESHOLD_MBPS and _can_fire_alert(entity, "flow_threshold"):
                alert_payload = {
                    "rule_name": "flow-threshold-exceeded",
                    "description": f"Flow threshold exceeded: {mbps:.1f} Mbps > {_FLOW_THRESHOLD_MBPS} Mbps",
                    "labels": {"exporter_ip": entity, "mbps": str(round(mbps, 1))},
                    "annotations": {"summary": f"High flow volume from {entity}"},
                }
                await self._publish_alert("high", alert_payload)

        elif record_type == "latency":
            latency_ms = float(data.get("latency_ms", 0))
            if latency_ms > _LATENCY_THRESHOLD_MS and _can_fire_alert(entity, "latency"):
                alert_payload = {
                    "rule_name": "latency-threshold-exceeded",
                    "description": (
                        f"Transit latency exceeded: {latency_ms:.1f} ms "
                        f"> {_LATENCY_THRESHOLD_MS} ms"
                    ),
                    "labels": {
                        "src_device": data.get("src_device", entity),
                        "dst_device": data.get("dst_device", "unknown"),
                        "latency_ms": str(round(latency_ms, 2)),
                    },
                    "annotations": {"summary": f"High latency between {entity} and peer"},
                }
                await self._publish_alert("medium", alert_payload)

        elif record_type in ("log", "trap"):
            _ERROR_KEYWORDS = {"error", "critical", "down", "unreachable"}
            # Search in body / message / syslog_msg fields
            body_text = " ".join(
                str(v)
                for k, v in data.items()
                if k in ("body", "message", "syslog_msg", "oid_resolved", "description")
            ).lower()
            matched = _ERROR_KEYWORDS.intersection(body_text.split())
            if matched and _can_fire_alert(entity, f"log_{next(iter(matched))}"):
                alert_payload = {
                    "rule_name": "log-anomaly-detected",
                    "description": f"Log anomaly detected: keywords {matched} in message from {entity}",
                    "labels": {"entity": entity, "keywords": ", ".join(sorted(matched))},
                    "annotations": {"summary": f"Anomalous log from {entity}"},
                }
                await self._publish_alert("low", alert_payload)

    async def _publish_alert(self, severity: str, payload: dict):
        """Publish a JSON alert payload to netpulse.alerts.<severity>."""
        if self._nc is None:
            logger.warning("stream-processor: cannot publish alert — NATS not connected")
            return
        try:
            subject = f"netpulse.alerts.{severity}"
            data = json.dumps(payload).encode()
            await self._nc.publish(subject, data)
            logger.info(
                "stream-processor: published alert severity=%s rule=%s",
                severity,
                payload.get("rule_name"),
            )
        except Exception as exc:
            logger.error("stream-processor: failed to publish alert: %s", exc)

    # -----------------------------------------------------------------------
    # Environment (CPU / memory / temperature / fan / PSU)
    # -----------------------------------------------------------------------

    @staticmethod
    def _derive_environment(payload: dict) -> dict:
        """Turn raw SNMP GET + walk results into normalized environment metrics."""
        from apps.telemetry.snmp_environment import derive_environment

        get_values = {
            oid: m.get("value")
            for oid, m in (payload.get("metrics") or {}).items()
            if isinstance(m, dict)
        }
        return derive_environment(get_values, payload.get("walk") or {})

    async def _write_environment(self, device_id: str, env: dict, timestamp):
        """Write device_environment points: per temperature sensor, per fan, per
        PSU, and one PoE summary. Status is stored as a string so 'unknown'
        (no per-unit sensor) round-trips distinctly from ok/fault."""
        for sensor in env.get("temperature", []):
            await self._write_influx(
                measurement="device_environment",
                tags={"device_id": device_id, "sensor_name": sensor["name"],
                      "sensor_type": "temperature"},
                fields={
                    "temperature_c": sensor["celsius"],
                    "status_ok": 1 if sensor["status_ok"] else 0,
                },
                timestamp=timestamp,
            )

        for fan in env.get("fans", []):
            await self._write_influx(
                measurement="device_environment",
                tags={"device_id": device_id, "sensor_name": fan["name"],
                      "sensor_type": "fan"},
                fields={
                    "fan_rpm": float(fan["rpm"]) if fan.get("rpm") is not None else -1.0,
                    "status": _unit_status_text(fan.get("status_ok"), "ok", "fault"),
                },
                timestamp=timestamp,
            )

        for psu in env.get("psus", []):
            await self._write_influx(
                measurement="device_environment",
                tags={"device_id": device_id, "sensor_name": psu["name"],
                      "sensor_type": "psu"},
                fields={
                    "watts": float(psu["watts"]) if psu.get("watts") is not None else -1.0,
                    "status": _unit_status_text(psu.get("status_ok"), "online", "offline"),
                },
                timestamp=timestamp,
            )

        poe = env.get("poe")
        if poe:
            fields = {
                "poe_budget_watts": float(poe.get("budget_watts") or 0),
                "poe_used_watts": float(poe.get("used_watts") or 0),
                "poe_status": poe.get("status") or "unknown",
            }
            if poe.get("used_pct") is not None:
                fields["poe_used_pct"] = float(poe["used_pct"])
            await self._write_influx(
                measurement="device_environment",
                tags={"device_id": device_id, "sensor_name": "poe",
                      "sensor_type": "poe"},
                fields=fields,
                timestamp=timestamp,
            )

    async def _check_temperature(self, device_id: str, hostname, env: dict):
        """Fire temperature alerts (warning / critical / sensor-failed)."""
        for sensor in env.get("temperature", []):
            name = sensor["name"]
            key = f"{device_id}:{sensor['index']}"
            labels = {"device_id": device_id, "hostname": hostname or "",
                      "sensor_name": name, "metric": "temperature_c"}
            if not sensor["status_ok"]:
                if _can_fire_alert(key, "temp_sensor_failed"):
                    await self._publish_alert("high", {
                        "rule_name": "Temperature Sensor Failed",
                        "description": f"Temperature sensor {name} is non-operational",
                        "labels": labels,
                        "annotations": {"summary": f"Sensor {name} failed on {hostname or device_id}"},
                    })
                continue
            celsius = sensor["celsius"]
            if celsius >= _TEMP_CRITICAL_C and _can_fire_alert(key, "temp_critical"):
                await self._publish_alert("critical", {
                    "rule_name": "High Temperature Critical",
                    "description": f"{name} at {celsius:.1f}°C ≥ {_TEMP_CRITICAL_C:.0f}°C",
                    "labels": {**labels, "temperature_c": str(celsius)},
                    "annotations": {"summary": f"Critical temperature on {hostname or device_id}"},
                })
            elif celsius >= _TEMP_WARNING_C and _can_fire_alert(key, "temp_warning"):
                await self._publish_alert("medium", {
                    "rule_name": "High Temperature Warning",
                    "description": f"{name} at {celsius:.1f}°C ≥ {_TEMP_WARNING_C:.0f}°C",
                    "labels": {**labels, "temperature_c": str(celsius)},
                    "annotations": {"summary": f"High temperature on {hostname or device_id}"},
                })

    # -----------------------------------------------------------------------
    # Utility helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _extract_fields(payload: dict) -> dict:
        """
        Extract numeric fields from a payload dict for InfluxDB.
        Skips string-only keys that are better suited as tags.
        """
        _TAG_KEYS = {
            "device_id", "exporter_ip", "protocol", "protocol_version",
            "service", "timestamp", "host", "source", "type", "version",
        }
        fields = {}
        for k, v in payload.items():
            if k in _TAG_KEYS:
                continue
            if isinstance(v, (int, float)):
                fields[k] = v
            elif isinstance(v, str):
                try:
                    fields[k] = float(v)
                except ValueError:
                    pass  # skip non-numeric strings

        # Parse nested metrics dict from SNMP poller
        nested = payload.get("metrics", {})
        for oid, m in nested.items():
            if not isinstance(m, dict):
                continue
            val = m.get("value")
            mib_type = m.get("type", "")
            name = m.get("name", oid).replace(".", "_").replace("-", "_")
            # Skip error values
            if not isinstance(val, (int, float)):
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    continue
            # Convert TimeTicks to seconds
            if mib_type == "TimeTicks":
                val = float(val) / 100.0
            fields[name] = val

        return fields


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
