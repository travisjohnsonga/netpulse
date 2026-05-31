import asyncio

import pytest

from apps.checks.models import CheckResult, ServiceCheck
from apps.checks.runner import (
    DEGRADED, DOWN, UP, check_ssh_banner, check_tcp, classify_status,
    dns_status, icmp_status, next_state, run_check, tls_status,
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
        # ldap has no handler yet (planned) → unsupported.
        r = asyncio.run(run_check({"check_type": "ldap", "host": "x", "effective_port": None,
                                   "config": {}, "timeout_seconds": 1}))
        assert r["status"] == DOWN and "unsupported" in r["error"]


# ── Stage 2: domain status helpers ──────────────────────────────────────────

class TestIcmpStatus:
    def test_up_low_loss(self):
        assert icmp_status(0.0, True) == UP
        assert icmp_status(5.0, True) == UP

    def test_degraded_mid_loss(self):
        assert icmp_status(10.0, True) == DEGRADED
        assert icmp_status(50.0, True) == DEGRADED

    def test_down_high_loss_or_dead(self):
        assert icmp_status(75.0, True) == DOWN
        assert icmp_status(0.0, False) == DOWN


class TestTlsStatus:
    def test_up_far_from_expiry(self):
        assert tls_status(90, 30, 7) == UP

    def test_degraded_within_warn(self):
        assert tls_status(20, 30, 7) == DEGRADED
        assert tls_status(3, 30, 7) == DEGRADED

    def test_down_expired_or_invalid(self):
        assert tls_status(0, 30, 7) == DOWN
        assert tls_status(-5, 30, 7) == DOWN
        assert tls_status(90, 30, 7, valid=False) == DOWN


class TestDnsStatus:
    def test_up_resolved_no_expectation(self):
        assert dns_status(True, None) == UP

    def test_up_resolved_matches(self):
        assert dns_status(True, True) == UP

    def test_degraded_resolved_mismatch(self):
        assert dns_status(True, False) == DEGRADED

    def test_down_unresolved(self):
        assert dns_status(False, None) == DOWN


def _run_ssh_against_local(server_banner=b"SSH-2.0-OpenSSH_8.9\r\n", expected=None):
    async def scenario():
        async def handle(reader, writer):
            writer.write(server_banner)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            await server.start_serving()
            cfg = {"expected_banner": expected} if expected else {}
            return await check_ssh_banner({"check_type": "ssh_banner", "host": "127.0.0.1",
                                           "effective_port": port, "timeout_seconds": 3, "config": cfg})
    return asyncio.run(scenario())


class TestSshBanner:
    def test_banner_up(self):
        r = _run_ssh_against_local()
        assert r["status"] == UP and r["details"]["banner"].startswith("SSH-2.0")

    def test_expected_banner_match(self):
        r = _run_ssh_against_local(expected="OpenSSH")
        assert r["status"] == UP

    def test_expected_banner_mismatch_degraded(self):
        r = _run_ssh_against_local(expected="Dropbear")
        assert r["status"] == DEGRADED

    def test_refused_is_down(self):
        r = asyncio.run(check_ssh_banner({"check_type": "ssh_banner", "host": "127.0.0.1",
                                          "effective_port": 1, "timeout_seconds": 2, "config": {}}))
        assert r["status"] == DOWN


class TestRunCheckPreservesDomainStatus:
    def test_latency_does_not_override_tls_degraded(self):
        # A handler-reported DEGRADED (e.g. TLS near expiry) must survive run_check
        # even though TLS connect time is tiny and no latency thresholds apply.
        import apps.checks.runner as runner

        async def fake_tls(check):
            return {"status": DEGRADED, "response_time_ms": 5.0, "error": "20 days remaining",
                    "details": {"days_remaining": 20}}

        orig = runner.HANDLERS["tls"]
        runner.HANDLERS["tls"] = fake_tls
        try:
            r = asyncio.run(run_check({"check_type": "tls", "host": "x", "effective_port": 443,
                                       "config": {}, "timeout_seconds": 5,
                                       "response_time_warning_ms": None, "response_time_critical_ms": None}))
        finally:
            runner.HANDLERS["tls"] = orig
        assert r["status"] == DEGRADED  # not upgraded to up

    def test_latency_downgrades_up_for_latency_type(self):
        # An "up" SSH probe slower than the critical threshold → down.
        import apps.checks.runner as runner

        async def fake_ssh(check):
            return {"status": UP, "response_time_ms": 3000.0, "error": "", "details": {}}

        orig = runner.HANDLERS["ssh_banner"]
        runner.HANDLERS["ssh_banner"] = fake_ssh
        try:
            r = asyncio.run(run_check({"check_type": "ssh_banner", "host": "x", "effective_port": 22,
                                       "config": {}, "timeout_seconds": 5,
                                       "response_time_warning_ms": 500, "response_time_critical_ms": 2000}))
        finally:
            runner.HANDLERS["ssh_banner"] = orig
        assert r["status"] == DOWN


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
