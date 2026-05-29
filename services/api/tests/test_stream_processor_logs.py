"""
Tests for the stream-processor's netpulse.logs.* handling (issue 2).

These exercise the log handler in isolation — no NATS/OpenSearch needed: the
OpenSearch client is None (so docs just accumulate in the buffer) and NATS is a
fake recorder.
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
    cmd._os_client = None          # buffer only, no real flush
    cmd._os_buffer = []
    cmd._os_buffer_lock = asyncio.Lock()
    return cmd


@pytest.fixture(autouse=True)
def _reset_dedup():
    sp._alert_last_fired.clear()
    yield
    sp._alert_last_fired.clear()


def _run(coro):
    return asyncio.run(coro)


def test_daily_index_format():
    idx = sp._daily_index("netpulse-logs")
    assert idx.startswith("netpulse-logs-")
    # netpulse-logs-YYYY.MM.DD
    assert len(idx.split("-")[-1].split(".")) == 3


def test_log_written_to_daily_index():
    cmd = _make_command()
    _run(cmd._on_log(FakeMsg("netpulse.logs.rtr-01", {"message": "interface up", "severity": "info"})))
    assert len(cmd._os_buffer) == 1
    index, doc = cmd._os_buffer[0]
    assert index.startswith("netpulse-logs-")
    assert doc["source"] == "rtr-01"
    assert doc["message"] == "interface up"
    assert "@timestamp" in doc
    # benign log → no auth event
    assert not [s for s, _ in cmd._nc.published if s == "netpulse.auth.events"]


def test_auth_failure_publishes_auth_event():
    cmd = _make_command()
    _run(cmd._on_log(FakeMsg(
        "netpulse.logs.fw-01",
        {"message": "Failed password for invalid user admin from 10.0.0.9", "src_ip": "10.0.0.9"},
    )))
    # still indexed
    assert len(cmd._os_buffer) == 1
    auth_events = [p for s, p in cmd._nc.published if s == "netpulse.auth.events"]
    assert len(auth_events) == 1
    assert auth_events[0]["source"] == "fw-01"
    assert auth_events[0]["src_ip"] == "10.0.0.9"


def test_anomaly_keyword_flags_alert():
    cmd = _make_command()
    _run(cmd._on_log(FakeMsg("netpulse.logs.sw-02", {"message": "LINK DOWN on Gi0/1, interface unreachable"})))
    alerts = [s for s, _ in cmd._nc.published if s.startswith("netpulse.alerts.")]
    assert alerts, "expected an anomaly alert to be published"


def test_benign_log_no_alert_no_auth():
    cmd = _make_command()
    _run(cmd._on_log(FakeMsg("netpulse.logs.host", {"message": "configuration saved successfully"})))
    assert len(cmd._os_buffer) == 1
    assert cmd._nc.published == []


def test_malformed_payload_does_not_raise():
    cmd = _make_command()
    bad = FakeMsg.__new__(FakeMsg)
    bad.subject = "netpulse.logs.x"
    bad.data = b"not json"
    # Should swallow the error, not raise.
    _run(cmd._on_log(bad))
    assert cmd._os_buffer == []
