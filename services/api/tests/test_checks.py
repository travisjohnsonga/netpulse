import asyncio

import pytest

from apps.checks.models import CheckResult, ServiceCheck
from apps.checks.runner import (
    DEGRADED, DOWN, UP, check_tcp, classify_status, next_state, run_check,
)
from apps.checks.service import check_to_dict, persist_result

pytestmark = pytest.mark.django_db


# ── Pure logic ──────────────────────────────────────────────────────────────

class TestClassifyStatus:
    def test_failed_probe_is_down(self):
        assert classify_status(False, None, 500, 1000) == DOWN

    def test_fast_probe_is_up(self):
        assert classify_status(True, 100.0, 500, 1000) == UP

    def test_over_warning_is_degraded(self):
        assert classify_status(True, 600.0, 500, 1000) == DEGRADED

    def test_over_critical_is_down(self):
        assert classify_status(True, 1200.0, 500, 1000) == DOWN

    def test_no_thresholds_is_up(self):
        assert classify_status(True, 9999.0, None, None) == UP


class TestNextState:
    def test_flap_suppression_holds_until_threshold(self):
        # failures_before_alert=2: first failure must NOT alert or flip to down.
        eff, fails, alert = next_state("up", 0, DOWN, 2)
        assert eff == "up" and fails == 1 and alert is None

    def test_confirmed_down_alerts(self):
        eff, fails, alert = next_state("up", 1, DOWN, 2)
        assert eff == DOWN and fails == 2 and alert == "down"

    def test_no_duplicate_down_alert(self):
        eff, fails, alert = next_state("down", 5, DOWN, 2)
        assert eff == DOWN and alert is None

    def test_recovery_alerts(self):
        eff, fails, alert = next_state("down", 5, UP, 2)
        assert eff == UP and fails == 0 and alert == "recovery"

    def test_degraded_alerts_once(self):
        eff, fails, alert = next_state("up", 0, DEGRADED, 2)
        assert eff == DEGRADED and alert == "degraded"
        eff2, _, alert2 = next_state("degraded", 0, DEGRADED, 2)
        assert eff2 == DEGRADED and alert2 is None


# ── Model ─────────────────────────────────────────────────────────────────────

class TestServiceCheckModel:
    def test_effective_port_default_from_type(self):
        c = ServiceCheck(name="web", check_type="https", host="x")
        assert c.effective_port == 443

    def test_effective_port_explicit_wins(self):
        c = ServiceCheck(name="db", check_type="tcp", host="x", port=5432)
        assert c.effective_port == 5432

    def test_tcp_has_no_default_port(self):
        assert ServiceCheck(name="t", check_type="tcp", host="x").effective_port is None


# ── TCP handler against a real local socket ─────────────────────────────────

def _run_tcp_against_local(send=None, expect=None, server_reply=b"PONG\r\n"):
    async def scenario():
        async def handle(reader, writer):
            await reader.read(64)
            writer.write(server_reply)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            await server.start_serving()
            cfg = {}
            if send:
                cfg["send"] = send
            if expect:
                cfg["expect"] = expect
            check = {"check_type": "tcp", "host": "127.0.0.1", "effective_port": port,
                     "timeout_seconds": 3, "config": cfg}
            return await check_tcp(check)
    return asyncio.run(scenario())


class TestTcpHandler:
    def test_tcp_connect_up(self):
        r = _run_tcp_against_local()
        assert r["status"] == UP and r["response_time_ms"] is not None
        assert "connect_time_ms" in r["details"]

    def test_tcp_expect_match(self):
        r = _run_tcp_against_local(send="PING\r\n", expect="PONG")
        assert r["status"] == UP and r["details"]["matched"] is True

    def test_tcp_expect_mismatch_is_down(self):
        r = _run_tcp_against_local(send="PING\r\n", expect="WELCOME")
        assert r["status"] == DOWN and r["details"]["matched"] is False

    def test_tcp_refused_is_down(self):
        # Port 1 is essentially never open → connection refused, not a crash.
        check = {"check_type": "tcp", "host": "127.0.0.1", "effective_port": 1,
                 "timeout_seconds": 2, "config": {}}
        r = asyncio.run(check_tcp(check))
        assert r["status"] == DOWN and r["error"]


class TestRunCheckDispatch:
    def test_unsupported_type_is_down(self):
        r = asyncio.run(run_check({"check_type": "icmp", "host": "x", "effective_port": None,
                                   "config": {}, "timeout_seconds": 1}))
        assert r["status"] == DOWN and "unsupported" in r["error"]


# ── persist_result state machine (DB) ───────────────────────────────────────

class TestPersistResult:
    def test_records_result_and_advances_state(self):
        from django.utils import timezone
        c = ServiceCheck.objects.create(name="web", check_type="https", host="example.com",
                                        failures_before_alert=2)
        now = timezone.now()
        # First failure: held (flap suppression), no alert.
        alert = persist_result(c, {"status": DOWN, "error": "timeout"}, now)
        c.refresh_from_db()
        assert alert is None and c.current_status == "unknown" and c.consecutive_failures == 1
        # Second failure: confirmed down + alert.
        alert = persist_result(c, {"status": DOWN, "error": "timeout"}, now)
        c.refresh_from_db()
        assert alert == "down" and c.current_status == "down"
        assert CheckResult.objects.filter(service_check=c).count() == 2
        # Recovery.
        alert = persist_result(c, {"status": UP, "response_time_ms": 12.0}, now)
        c.refresh_from_db()
        assert alert == "recovery" and c.current_status == "up" and c.consecutive_failures == 0


# ── API ─────────────────────────────────────────────────────────────────────

class TestChecksApi:
    def test_create_and_list(self, auth_client):
        resp = auth_client.post("/api/checks/", {
            "name": "Company Website", "check_type": "https", "host": "app.co",
        }, format="json")
        assert resp.status_code == 201, resp.content
        assert resp.json()["effective_port"] == 443
        lst = auth_client.get("/api/checks/")
        assert lst.status_code == 200 and lst.json()["count"] == 1

    def test_summary(self, auth_client):
        ServiceCheck.objects.create(name="a", check_type="tcp", host="h", current_status="up")
        ServiceCheck.objects.create(name="b", check_type="tcp", host="h", current_status="down")
        ServiceCheck.objects.create(name="c", check_type="tcp", host="h", current_status="up")
        body = auth_client.get("/api/checks/summary/").json()
        assert body["up"] == 2 and body["down"] == 1 and body["total"] == 3

    def test_run_now(self, auth_client, monkeypatch):
        c = ServiceCheck.objects.create(name="t", check_type="tcp", host="h", port=9, failures_before_alert=1)

        async def fake_run(check):
            return {"status": UP, "response_time_ms": 5.0, "error": "", "details": {"connect_time_ms": 5.0}}

        monkeypatch.setattr("apps.checks.views.run_check", fake_run)
        resp = auth_client.post(f"/api/checks/{c.id}/run-now/")
        assert resp.status_code == 200 and resp.json()["status"] == "up"
        c.refresh_from_db()
        assert c.current_status == "up" and c.last_checked is not None

    def test_results_history(self, auth_client):
        from django.utils import timezone
        c = ServiceCheck.objects.create(name="t", check_type="tcp", host="h")
        CheckResult.objects.create(service_check=c, status="up", checked_at=timezone.now(), response_time_ms=10)
        body = auth_client.get(f"/api/checks/{c.id}/results/?period=24h").json()
        assert body["count"] == 1 and body["results"][0]["check"] == c.id

    def test_check_to_dict_shape(self):
        c = ServiceCheck.objects.create(name="t", check_type="http", host="h", port=8080,
                                        config={"path": "/health"})
        d = check_to_dict(c)
        assert d["effective_port"] == 8080 and d["config"]["path"] == "/health"
