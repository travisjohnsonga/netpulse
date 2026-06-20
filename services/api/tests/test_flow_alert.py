"""
Tests for the stream-processor's NetFlow/sFlow volume alerting (_on_flow +
_check_anomalies "flow_threshold").

Regressions covered:
  * exporter_ip was truncated to its first octet ("10") because the NATS subject
    netpulse.flows.<ip>.<type> splits an IPv4 across tokens — it now comes from
    the payload, and the flow type is read from the LAST subject token.
  * a 0/near-0-duration record fabricated hundreds of Gbps — the duration is now
    floored so a short record can't masquerade as a threshold breach.
  * the alert is enriched with the device hostname + top-talker context.

No NATS/InfluxDB/OpenSearch needed: NATS is a fake recorder, the OpenSearch
client is None (docs just buffer), and the device lookup is monkeypatched.
"""
import asyncio
import json

import pytest

from apps.telemetry.management.commands import run_stream_processor as sp


class FakeMsg:
    def __init__(self, subject, payload):
        self.subject = subject
        self.data = json.dumps(payload).encode()


class FakeNATS:
    def __init__(self):
        self.published = []

    async def publish(self, subject, data):
        self.published.append((subject, json.loads(data)))


def _make_command():
    cmd = sp.Command()
    cmd._nc = FakeNATS()
    cmd._os_client = None
    cmd._os_buffer = []
    cmd._os_buffer_lock = asyncio.Lock()
    cmd._influx_write_api = None
    return cmd


@pytest.fixture(autouse=True)
def _reset_dedup():
    sp._alert_last_fired.clear()
    yield
    sp._alert_last_fired.clear()


@pytest.fixture(autouse=True)
def _no_device_lookup(monkeypatch):
    """Default: exporter IP resolves to no device (keeps tests DB-free)."""
    async def _none(ip):
        return None
    monkeypatch.setattr(sp, "_device_for_ip", _none)


def _run(coro):
    return asyncio.run(coro)


# ── exporter_ip is no longer truncated ───────────────────────────────────────

def test_exporter_ip_is_full_not_truncated():
    cmd = _make_command()
    # 200 MB over a 1s window = 1600 Mbps > 1000 Mbps default threshold.
    _run(cmd._on_flow(FakeMsg(
        "netpulse.flows.10.150.0.12.netflow5",
        {"exporter_ip": "10.150.0.12", "bytes": 200_000_000, "duration_ms": 1000,
         "src_ip": "10.0.0.5", "dst_ip": "8.8.8.8"},
    )))
    alerts = [p for s, p in cmd._nc.published if s == "netpulse.alerts.high"]
    assert len(alerts) == 1
    labels = alerts[0]["labels"]
    assert labels["exporter_ip"] == "10.150.0.12"   # not "10"
    assert labels["source"] == "flow_monitor"
    # top-talker context surfaced
    assert labels["top_source"] == "10.0.0.5"
    assert labels["top_destination"] == "8.8.8.8"
    assert "10.0.0.5 → 8.8.8.8" == alerts[0]["annotations"]["top_talker"]


# ── short/zero-duration records can't fabricate a breach ─────────────────────

def test_zero_duration_record_does_not_explode():
    cmd = _make_command()
    # 40 bytes, no duration → previously /1ms = ~0.32 Mbps*1000 phantom Gbps.
    _run(cmd._on_flow(FakeMsg(
        "netpulse.flows.10.150.0.12.netflow5",
        {"exporter_ip": "10.150.0.12", "bytes": 40, "duration_ms": 0},
    )))
    assert [p for s, p in cmd._nc.published if s.startswith("netpulse.alerts.")] == []
    # still indexed to OpenSearch buffer
    assert len(cmd._os_buffer) == 1


# ── flow type comes from the LAST token even with a dotted IP ─────────────────

def test_latency_subject_routed_despite_dotted_ip():
    cmd = _make_command()
    _run(cmd._on_flow(FakeMsg(
        "netpulse.flows.10.150.0.12.latency",
        {"src_device": "10.150.0.12", "dst_device": "10.150.0.20", "latency_ms": 999},
    )))
    # latency path does NOT buffer a flow doc; it fires a latency alert.
    assert cmd._os_buffer == []
    rules = [p["rule_name"] for s, p in cmd._nc.published if s.startswith("netpulse.alerts.")]
    assert rules == ["latency-threshold-exceeded"]


# ── hostname enrichment when the exporter resolves to a device ────────────────

def test_alert_enriched_with_device_hostname(monkeypatch):
    cmd = _make_command()

    async def _fake(ip):
        assert ip == "10.150.0.15"
        return {"id": 42, "hostname": "wco2-mdf-crt-01"}
    monkeypatch.setattr(sp, "_device_for_ip", _fake)

    _run(cmd._on_flow(FakeMsg(
        "netpulse.flows.10.150.0.15.netflow5",
        {"exporter_ip": "10.150.0.15", "bytes": 300_000_000, "duration_ms": 1000},
    )))
    alert = [p for s, p in cmd._nc.published if s == "netpulse.alerts.high"][0]
    assert alert["labels"]["hostname"] == "wco2-mdf-crt-01"
    assert alert["labels"]["device"] == "wco2-mdf-crt-01"
    assert alert["labels"]["device_id"] == "42"
    assert alert["annotations"]["title"] == "High flow volume from wco2-mdf-crt-01"
    assert "wco2-mdf-crt-01 (10.150.0.15)" in alert["annotations"]["message"]


def test_device_lookup_helper_resolves_by_either_ip(db):
    """_lookup_device_for_ip matches management_ip OR ip_address."""
    from apps.devices.models import Device
    d = Device.objects.create(hostname="r1", ip_address="192.0.2.10",
                              management_ip="10.9.9.9")
    assert sp._lookup_device_for_ip("10.9.9.9")["id"] == d.id     # management_ip
    assert sp._lookup_device_for_ip("192.0.2.10")["id"] == d.id   # ip_address
    assert sp._lookup_device_for_ip("203.0.113.1") is None
    assert sp._lookup_device_for_ip("unknown") is None
    assert sp._lookup_device_for_ip("") is None
