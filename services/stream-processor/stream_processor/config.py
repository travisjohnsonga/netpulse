import os

NATS_URL       = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_USER      = os.environ.get("NATS_USER", "")
NATS_PASSWORD  = os.environ.get("NATS_PASSWORD", "")

INFLUX_URL     = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN   = os.environ.get("INFLUXDB_ADMIN_TOKEN", "")
INFLUX_ORG     = os.environ.get("INFLUXDB_ORG", "netpulse")
INFLUX_BUCKET  = os.environ.get("INFLUXDB_BUCKET", "metrics")

OPENSEARCH_URL  = os.environ.get("OPENSEARCH_URL", "http://opensearch:9200")
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USER", "admin")
OPENSEARCH_PASS = os.environ.get("OPENSEARCH_PASSWORD", "")

BATCH_SIZE    = int(os.environ.get("STREAM_PROCESSOR_BATCH_SIZE", "100"))
BATCH_TIMEOUT = float(os.environ.get("STREAM_PROCESSOR_BATCH_TIMEOUT_SECONDS", "5"))

FLOW_THRESHOLD_MBPS  = float(os.environ.get("ANOMALY_FLOW_THRESHOLD_MBPS", "1000"))
LATENCY_THRESHOLD_MS = float(os.environ.get("ANOMALY_LATENCY_THRESHOLD_MS", "500"))

ALERT_COOLDOWN_S = float(os.environ.get("ALERT_COOLDOWN_SECONDS", "300"))
