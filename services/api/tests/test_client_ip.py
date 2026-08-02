"""
Spoof-resistant client-IP derivation (apps.core.client_ip.get_client_ip).

The forensic IP (audit log / failed-login records) must be as trustworthy as the
DRF throttle IP: a forged leading X-Forwarded-For entry must NOT end up anywhere.
These tests pin the right-most-hop semantics, the fail-closed fallback, and that
both the audit path and the throttle path go through the one shared helper.
"""
import pytest
from django.test import RequestFactory, override_settings

from apps.core.client_ip import TrustedProxyScopedRateThrottle, get_client_ip

# A request shaped like what nginx (1 trusted proxy) actually delivers: the client
# forged "1.2.3.4" as the leading XFF entry; nginx appended the attacker's real
# socket IP (203.0.113.7) as the right-most entry. REMOTE_ADDR is the nginx peer.
FORGED = "1.2.3.4"
REAL_CLIENT = "203.0.113.7"
PROXY_ADDR = "172.18.0.5"

rf = RequestFactory()


def _req(xff=None, remote=PROXY_ADDR):
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return rf.get("/", **extra)


class TestGetClientIpDefault:
    """NUM_PROXIES=1 (the lab/single-nginx default in settings)."""

    def test_forged_leading_entry_is_ignored(self):
        ip = get_client_ip(_req(f"{FORGED}, {REAL_CLIENT}"))
        assert ip == REAL_CLIENT
        assert ip != FORGED  # the spoof must not survive

    def test_single_proxy_returns_real_client(self):
        # nginx appended exactly one entry (no client-supplied XFF).
        assert get_client_ip(_req(REAL_CLIENT)) == REAL_CLIENT

    def test_absent_xff_falls_back_to_remote_addr(self):
        assert get_client_ip(_req(xff=None)) == PROXY_ADDR

    def test_malformed_xff_falls_back_to_remote_addr(self):
        assert get_client_ip(_req(" , , ")) == PROXY_ADDR

    def test_none_request_returns_none(self):
        assert get_client_ip(None) is None

    def test_no_xff_no_remote_returns_none(self):
        assert get_client_ip(_req(xff=None, remote="")) is None


class TestGetClientIpTwoProxies:
    """Behind 2 trusted proxies (e.g. LB + nginx). override_settings sends the
    setting_changed signal that DRF uses to reload api_settings.NUM_PROXIES."""

    def test_short_xff_fails_closed_to_remote_addr(self):
        # Only 1 hop present but 2 are expected → header is untrustworthy.
        with override_settings(REST_FRAMEWORK={"NUM_PROXIES": 2}):
            ip = get_client_ip(_req(FORGED))
        assert ip == PROXY_ADDR
        assert ip != FORGED  # never trust a too-short, attacker-influenced header

    def test_counts_two_from_the_right(self):
        # client forged "1.2.3.4"; LB appended the real client; nginx appended LB.
        with override_settings(REST_FRAMEWORK={"NUM_PROXIES": 2}):
            ip = get_client_ip(_req(f"{FORGED}, {REAL_CLIENT}, 10.0.0.9"))
        assert ip == REAL_CLIENT
        assert ip != FORGED


class TestGetClientIpNoProxies:
    def test_zero_proxies_always_uses_remote_addr(self):
        # Direct exposure: XFF is entirely client-controlled and must be ignored.
        with override_settings(REST_FRAMEWORK={"NUM_PROXIES": 0}):
            assert get_client_ip(_req(f"{FORGED}, {REAL_CLIENT}")) == PROXY_ADDR


class TestThrottleSharesHelper:
    """The throttle path and the forensic path must derive the SAME IP."""

    def test_throttle_get_ident_matches_get_client_ip(self):
        req = _req(f"{FORGED}, {REAL_CLIENT}")
        throttle = TrustedProxyScopedRateThrottle()
        assert throttle.get_ident(req) == REAL_CLIENT == get_client_ip(req)
        assert throttle.get_ident(req) != FORGED


@pytest.mark.django_db
class TestAuditForensicIp:
    """The IP stamped into AuditLog must be the trusted hop, not the forged one."""

    def test_log_event_records_trusted_ip(self):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog

        req = _req(f"{FORGED}, {REAL_CLIENT}")
        row = log_event(
            AuditLog.EventType.LOGIN_FAILED, request=req,
            username="attacker", description="t", success=False,
        )
        assert row.ip_address == REAL_CLIENT
        assert row.ip_address != FORGED

    def test_failed_login_audit_ip_is_not_spoofable(self, api_client):
        """End-to-end: a forged X-Forwarded-For on a bad login is NOT recorded."""
        from apps.core.models import AuditLog

        resp = api_client.post(
            "/api/auth/token/",
            {"username": "nobody", "password": "wrong"},
            HTTP_X_FORWARDED_FOR=f"{FORGED}, {REAL_CLIENT}",
        )
        assert resp.status_code == 401  # bad creds
        row = AuditLog.objects.filter(
            event_type=AuditLog.EventType.LOGIN_FAILED).latest("created_at")
        assert row.ip_address == REAL_CLIENT
        assert row.ip_address != FORGED  # the spoofed audit record is prevented
