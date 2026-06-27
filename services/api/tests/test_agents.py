"""Tests for the NetPulse Agent app (enrollment, ingestion, roles, tokens)."""
import pytest

from apps.agents import pki, views as agent_views
from apps.agents.metrics import build_points
from apps.agents.models import Agent, AgentEnrollmentToken, AgentRole, ServerRole
from apps.devices.models import Device

pytestmark = pytest.mark.django_db

CSR = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB...\n-----END CERTIFICATE REQUEST-----"


@pytest.fixture
def fake_pki(monkeypatch):
    """Stub OpenBao PKI signing so enrollment works without a live engine."""
    def _issue(hostname, csr_pem, ttl="8760h"):
        return {"certificate": f"-----BEGIN CERTIFICATE-----\n{hostname}\n-----END CERTIFICATE-----",
                "ca_chain": ["-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----"],
                "serial": "ab:cd:ef:01", "expiration": None}
    monkeypatch.setattr(pki, "issue_agent_certificate", _issue)
    monkeypatch.setattr(agent_views.pki, "issue_agent_certificate", _issue)


def _token(**kw):
    return AgentEnrollmentToken.objects.create(**kw)


def _enroll(api_client, token, hostname="web-01", **extra):
    body = {"enrollment_token": token, "hostname": hostname, "os": "linux",
            "arch": "amd64", "version": "1.0.0", "csr": CSR, **extra}
    return api_client.post("/api/agents/enroll/", body, format="json")


# ── Built-in roles seed ───────────────────────────────────────────────────────

class TestServerRoles:
    def test_builtin_roles_seeded(self):
        roles = ServerRole.objects.filter(is_builtin=True)
        assert roles.count() >= 7
        dns = roles.get(role_type="dns")
        assert "named" in dns.linux_services and {"port": 53, "proto": "udp", "name": "DNS"} in dns.port_checks

    def test_list_requires_auth(self, api_client):
        assert api_client.get("/api/agents/roles/").status_code == 401

    def test_create_and_delete_custom_role(self, auth_client):
        resp = auth_client.post("/api/agents/roles/", {
            "name": "My App", "role_type": "custom", "linux_services": ["myapp"],
        }, format="json")
        assert resp.status_code == 201, resp.content
        rid = resp.json()["id"]
        assert auth_client.delete(f"/api/agents/roles/{rid}/").status_code == 204

    def test_cannot_delete_builtin_role(self, auth_client):
        builtin = ServerRole.objects.filter(is_builtin=True).first()
        resp = auth_client.delete(f"/api/agents/roles/{builtin.id}/")
        assert resp.status_code == 400
        assert ServerRole.objects.filter(id=builtin.id).exists()


# ── Enrollment tokens ─────────────────────────────────────────────────────────

class TestTokens:
    def test_create_reveals_full_token_once(self, auth_client):
        resp = auth_client.post("/api/agents/tokens/", {"description": "prod web", "max_uses": 5},
                                format="json")
        assert resp.status_code == 201
        tok = resp.json()["token"]
        assert "…" not in tok and len(tok) > 20  # full token on create

    def test_list_masks_token(self, auth_client):
        _token(description="x")
        data = auth_client.get("/api/agents/tokens/").json()
        items = data["results"] if isinstance(data, dict) else data
        assert items[0]["token"].endswith("…")

    def test_target_os_defaults_any_and_is_settable(self, auth_client):
        # Default 'any' when omitted; honored when provided.
        r1 = auth_client.post("/api/agents/tokens/", {"description": "d"}, format="json")
        assert r1.status_code == 201 and r1.json()["target_os"] == "any"
        r2 = auth_client.post("/api/agents/tokens/",
                              {"description": "w", "target_os": "windows"}, format="json")
        assert r2.status_code == 201 and r2.json()["target_os"] == "windows"

    def test_is_valid_rules(self):
        from django.utils import timezone
        from datetime import timedelta
        t = _token(max_uses=1, use_count=0)
        assert t.is_valid()
        t.use_count = 1
        assert not t.is_valid()  # exhausted
        t2 = _token(max_uses=0, use_count=99)
        assert t2.is_valid()  # 0 = unlimited
        t3 = _token(expires_at=timezone.now() - timedelta(hours=1))
        assert not t3.is_valid()  # expired
        t4 = _token(is_active=False)
        assert not t4.is_valid()


# ── Enrollment ────────────────────────────────────────────────────────────────

class TestEnroll:
    def test_enroll_creates_agent_and_device(self, api_client, fake_pki):
        t = _token(max_uses=1)
        resp = _enroll(api_client, t.token, hostname="web-01")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["agent_id"] and body["certificate"].startswith("-----BEGIN CERTIFICATE")
        assert body["collection_interval"] == 30 and body["server_url"].startswith("https://")
        agent = Agent.objects.get(id=body["agent_id"])
        assert agent.cert_serial == "ab:cd:ef:01" and agent.os == "linux"
        # Auto-created device linked (APIClient REMOTE_ADDR = 127.0.0.1).
        assert agent.device is not None and agent.device.hostname == "web-01"
        # Single-use token consumed + deactivated.
        t.refresh_from_db()
        assert t.use_count == 1 and t.is_active is False

    def test_enroll_stores_os_detail(self, api_client, fake_pki):
        t = _token()
        resp = _enroll(api_client, t.token, hostname="ubu-01",
                       os_name="Ubuntu 22.04.3 LTS", os_version="22.04", kernel="6.8.0-31")
        assert resp.status_code == 201, resp.content
        agent = Agent.objects.get(id=resp.json()["agent_id"])
        assert agent.os == "linux"          # os_family unchanged
        assert agent.os_name == "Ubuntu 22.04.3 LTS"
        assert agent.os_version == "22.04"
        assert agent.os_kernel == "6.8.0-31"

    def test_enroll_old_agent_without_os_detail(self, api_client, fake_pki):
        # Backward-compat: a pre-OS-detail agent omits os_name/os_version/kernel.
        t = _token()
        resp = _enroll(api_client, t.token, hostname="old-01")  # no os_* extras
        assert resp.status_code == 201, resp.content
        agent = Agent.objects.get(id=resp.json()["agent_id"])
        assert agent.os == "linux" and agent.os_name == "" and agent.os_version == ""

    def test_enroll_invalid_token_403(self, api_client, fake_pki):
        assert _enroll(api_client, "nope").status_code == 403

    def test_enroll_exhausted_token_403(self, api_client, fake_pki):
        t = _token(max_uses=1, use_count=1, is_active=False)
        assert _enroll(api_client, t.token).status_code == 403

    def test_enroll_pki_failure_502(self, api_client, monkeypatch):
        def _boom(*a, **k):
            raise pki.AgentPKIError("PKI down")
        monkeypatch.setattr(agent_views.pki, "issue_agent_certificate", _boom)
        t = _token()
        assert _enroll(api_client, t.token).status_code == 502

    def test_second_agent_same_ip_enrolls_without_device(self, api_client, fake_pki):
        t = _token(max_uses=0)
        _enroll(api_client, t.token, hostname="host-a")
        resp = _enroll(api_client, t.token, hostname="host-b")
        assert resp.status_code == 201
        # IP already owned by host-a → host-b enrolls but gets no auto device.
        assert Agent.objects.get(hostname="host-b").device is None
        assert Device.objects.filter(hostname="host-b").count() == 0

    def test_reenroll_same_host_reuses_agent(self, api_client, fake_pki, monkeypatch):
        # Re-running the installer on an already-enrolled host updates the same
        # agent record (rotates the cert) instead of 500-ing on the device link.
        t = _token(max_uses=0)
        first = _enroll(api_client, t.token, hostname="web-01")
        assert first.status_code == 201
        aid = first.json()["agent_id"]
        # Second enrollment returns a fresh cert serial → 200 (re_enrolled), same id.
        monkeypatch.setattr(agent_views.pki, "issue_agent_certificate",
                            lambda *a, **k: {"serial": "ff:ee:dd:cc", "certificate":
                            "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----", "ca_chain": []})
        second = _enroll(api_client, t.token, hostname="web-01")
        assert second.status_code == 200, second.content
        assert second.json()["agent_id"] == aid and second.json()["re_enrolled"] is True
        assert Agent.objects.filter(hostname="web-01").count() == 1
        a = Agent.objects.get(id=aid)
        assert a.cert_serial == "ff:ee:dd:cc" and a.device is not None

    def test_reenroll_revoked_host_creates_fresh_and_steals_device(self, api_client, fake_pki):
        t = _token(max_uses=0)
        first = _enroll(api_client, t.token, hostname="web-02")
        old = Agent.objects.get(id=first.json()["agent_id"])
        old.status = Agent.Status.REVOKED
        old.save(update_fields=["status"])
        # A revoked host re-enrolling gets a NEW agent; the device link transfers
        # to it (no OneToOne 500).
        resp = _enroll(api_client, t.token, hostname="web-02")
        assert resp.status_code == 201
        new = Agent.objects.get(id=resp.json()["agent_id"])
        assert new.id != old.id and new.device is not None
        old.refresh_from_db()
        assert old.device is None


# ── Metrics + role-check ingestion (client-cert authed) ─────────────────────────

class TestIngestion:
    def _agent(self, serial="ab:cd:ef:01"):
        return Agent.objects.create(hostname="srv-1", cert_serial=serial)

    def test_metrics_requires_matching_cert_serial(self, api_client, monkeypatch):
        def fake_write(*a, **k):
            return 3
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", fake_write)
        a = self._agent()
        # No mTLS headers → 403 (mTLS authenticator has no challenge header, so
        # DRF renders the unauthenticated denial as 403, not 401)
        assert api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json").status_code == 403
        # Verified but wrong serial → 403
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json",
                            HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="wrong")
        assert r.status_code == 403
        # Serial present but nginx didn't verify (no X-Agent-Verified) → 403
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json",
                            HTTP_X_AGENT_CERT_SERIAL="ab:cd:ef:01")
        assert r.status_code == 403
        # Verified + correct serial (uppercase, no colons — nginx format) → 200
        r = api_client.post(f"/api/agents/{a.id}/metrics/",
                            {"metrics": {"cpu": [{"core": "cpu", "usage_pct": 12}]}}, format="json",
                            HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")
        assert r.status_code == 200 and r.json()["points_written"] == 3
        a.refresh_from_db()
        assert a.last_seen is not None

    def test_metrics_response_pushes_assigned_roles(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent()
        hdr = dict(HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")
        # No roles assigned → role checks stay disabled.
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json", **hdr).json()
        assert r["assigned_roles"] == []
        assert r["collection_config"]["role_checks_enabled"] is False
        assert r["collection_config"]["services"] is True
        # Assign a role → it is pushed back and role checks are enabled.
        AgentRole.objects.create(agent=a, role=ServerRole.objects.get(role_type="web"))
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json", **hdr).json()
        assert r["assigned_roles"] == ["web"]
        assert r["collection_config"]["role_checks_enabled"] is True

    def test_revoked_agent_rejected(self, api_client):
        a = self._agent()
        a.status = Agent.Status.REVOKED
        a.save()
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json",
                            HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ab:cd:ef:01")
        assert r.status_code == 403

    def test_role_checks_stored_and_returned(self, api_client, auth_client):
        a = self._agent()
        r = api_client.post(f"/api/agents/{a.id}/role-checks/", {
            "roles": [{"role": "dns", "services": [{"name": "named", "running": True}],
                       "ports": [{"port": 53, "proto": "udp", "open": True}], "custom": []}],
        }, format="json", HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ab:cd:ef:01")
        assert r.status_code == 200 and r.json()["roles"] == 1
        # Authenticated read of role status
        got = auth_client.get(f"/api/agents/{a.id}/roles/").json()
        assert got[0]["role_type"] == "dns" and got[0]["services"][0]["name"] == "named"


# ── Server role assignment (/api/servers/) ──────────────────────────────────────

class TestServerRoleAssignment:
    def _server(self, **kw):
        kw.setdefault("os", "linux")
        return Agent.objects.create(hostname="srv-roles", **kw)

    def test_assign_list_remove_role(self, auth_client):
        s = self._server()
        dns = ServerRole.objects.get(role_type="dns")
        r = auth_client.post(f"/api/servers/{s.id}/roles/", {"role_id": dns.id}, format="json")
        assert r.status_code == 201, r.content
        got = auth_client.get(f"/api/servers/{s.id}/roles/").json()
        assert len(got) == 1 and got[0]["role_type"] == "dns" and got[0]["role_id"] == dns.id
        # Re-assign is idempotent → 200
        assert auth_client.post(f"/api/servers/{s.id}/roles/", {"role_id": dns.id}, format="json").status_code == 200
        assert auth_client.delete(f"/api/servers/{s.id}/roles/{dns.id}/").status_code == 204
        assert auth_client.get(f"/api/servers/{s.id}/roles/").json() == []

    def test_assign_unknown_role_400(self, auth_client):
        s = self._server()
        assert auth_client.post(f"/api/servers/{s.id}/roles/", {"role_id": 999999}, format="json").status_code == 400

    def test_detect_roles_from_reported_services(self, auth_client):
        s = self._server(reported_services=["named", "sshd"])
        detected = auth_client.post(f"/api/servers/{s.id}/detect-roles/").json()["detected"]
        dns = next((d for d in detected if d["role_type"] == "dns"), None)
        assert dns and "named" in dns["matched_services"]
        assert dns["confidence"] > 0 and dns["assigned"] is False

    def test_role_checks_auto_assigns_role(self, api_client):
        a = Agent.objects.create(hostname="srv-auto", os="linux", cert_serial="ab:cd:ef:01")
        r = api_client.post(f"/api/agents/{a.id}/role-checks/",
                            {"roles": [{"role": "dns", "services": [], "ports": []}]}, format="json",
                            HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ab:cd:ef:01")
        assert r.status_code == 200
        assert AgentRole.objects.filter(agent=a, role__role_type="dns", auto_detected=True).exists()

    def test_metrics_populates_reported_services(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = Agent.objects.create(hostname="srv-svc", os="linux", cert_serial="ab:cd:ef:01")
        api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {"services": ["named", "nginx"]}},
                        format="json", HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ab:cd:ef:01")
        a.refresh_from_db()
        assert a.reported_services == ["named", "nginx"]

    def test_server_roles_require_auth(self, api_client):
        s = self._server()
        assert api_client.get(f"/api/servers/{s.id}/roles/").status_code == 401


class TestServersApi:
    def test_list_returns_servers_with_metric_keys(self, auth_client):
        Agent.objects.create(hostname="web-1", os="linux", version="1.0.0")
        data = auth_client.get("/api/servers/").json()
        items = data["results"] if isinstance(data, dict) else data
        s = next(x for x in items if x["hostname"] == "web-1")
        assert s["agent_version"] == "1.0.0" and "roles" in s
        assert {"cpu_pct", "memory_pct", "load_1", "disk_max_pct", "disk_max_mount"} <= set(s["latest_metrics"])

    def test_detail_has_metrics_and_alerts(self, auth_client):
        a = Agent.objects.create(hostname="web-2", os="linux")
        d = auth_client.get(f"/api/servers/{a.id}/").json()
        assert "detail_metrics" in d and "recent_alerts" in d

    def test_history_endpoint(self, auth_client):
        a = Agent.objects.create(hostname="web-3", os="linux")
        d = auth_client.get(f"/api/servers/{a.id}/metrics/history/?metric=cpu&range=1h").json()
        assert d["metric"] == "cpu" and "series" in d

    def test_revoked_excluded(self, auth_client):
        Agent.objects.create(hostname="gone", os="linux", status=Agent.Status.REVOKED)
        data = auth_client.get("/api/servers/").json()
        items = data["results"] if isinstance(data, dict) else data
        assert all(x["hostname"] != "gone" for x in items)

    def test_servers_require_auth(self, api_client):
        assert api_client.get("/api/servers/").status_code == 401


# ── OS-detail: refresh-on-push + serializer exposure + backward-compat ────────────

class TestOSDetail:
    _HDR = dict(HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")

    def _agent(self, **kw):
        return Agent.objects.create(hostname="srv-os", cert_serial="ab:cd:ef:01", **kw)

    def test_metrics_refreshes_os_detail(self, api_client, monkeypatch):
        # An in-place OS upgrade self-corrects: the push carries metrics.system.
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(os="linux", os_name="Ubuntu 20.04.6 LTS", os_version="20.04")
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {"system": {
            "os": "linux", "os_name": "Ubuntu 22.04.3 LTS", "os_version": "22.04",
            "kernel": "6.8.0-31"}}}, format="json", **self._HDR)
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.os_name == "Ubuntu 22.04.3 LTS" and a.os_version == "22.04"
        assert a.os_kernel == "6.8.0-31"

    def test_metrics_without_system_keeps_os_detail(self, api_client, monkeypatch):
        # Older agent (no system block / no os_name) must not blank good data.
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(os="linux", os_name="Ubuntu 22.04.3 LTS", os_version="22.04")
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}},
                            format="json", **self._HDR)
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.os_name == "Ubuntu 22.04.3 LTS"  # not blanked

    def test_agent_serializer_exposes_os_detail(self, auth_client):
        Agent.objects.create(hostname="ser-os", cert_serial="s1",
                             os="linux", os_name="AlmaLinux 9.4", os_version="9.4")
        rows = auth_client.get("/api/agents/").json()
        rows = rows.get("results", rows)
        row = next(a for a in rows if a["hostname"] == "ser-os")
        assert row["os"] == "linux" and row["os_name"] == "AlmaLinux 9.4"
        assert row["os_version"] == "9.4"

    def test_server_serializer_prefers_agent_os(self, auth_client):
        # ServerSerializer.os_version prefers the agent's own value (not Device).
        Agent.objects.create(hostname="srv-detail", cert_serial="s2",
                             os="linux", os_name="Debian 12", os_version="12")
        servers = auth_client.get("/api/servers/").json()
        servers = servers.get("results", servers)
        row = next(s for s in servers if s["hostname"] == "srv-detail")
        assert row["os_name"] == "Debian 12" and row["os_version"] == "12"

    def test_old_agent_serializes_with_blank_os_detail(self, auth_client):
        # Pre-OS-detail agent: os_name/os_version blank, os still present → UI falls back.
        Agent.objects.create(hostname="legacy-os", cert_serial="s3", os="linux")
        rows = auth_client.get("/api/agents/").json()
        rows = rows.get("results", rows)
        row = next(a for a in rows if a["hostname"] == "legacy-os")
        assert row["os"] == "linux" and row["os_name"] == "" and row["os_version"] == ""


# ── Agent liveness alerting (offline detection via the existing alert plumbing) ───

class TestAgentLiveness:
    def _agent(self, secs_ago=None, **kw):
        from django.utils import timezone
        from datetime import timedelta
        last = None if secs_ago is None else timezone.now() - timedelta(seconds=secs_ago)
        return Agent.objects.create(hostname="srv-live", cert_serial="lv1",
                                    last_seen=last, **kw)

    def _fire_count(self):
        from apps.alerts.models import AlertEvent
        return AlertEvent.objects.filter(state=AlertEvent.State.FIRING,
                                         labels__alert_type="agent_offline").count()

    def test_stale_agent_fires_offline_alert(self):
        from apps.agents.liveness import reconcile_agent_liveness
        from apps.alerts.models import AlertEvent
        self._agent(secs_ago=600)  # > 300s default
        res = reconcile_agent_liveness()
        assert res["fired"] == 1
        ev = AlertEvent.objects.get(labels__alert_type="agent_offline")
        # Reuses the existing AlertEvent/AlertRule plumbing (not a parallel system).
        assert ev.state == AlertEvent.State.FIRING
        assert ev.rule.name == "Agent Offline"
        assert ev.labels["source"] == "agent_liveness"
        assert ev.labels["hostname"] == "srv-live"

    def test_fresh_agent_does_not_fire(self):
        from apps.agents.liveness import reconcile_agent_liveness
        self._agent(secs_ago=30)  # well within 300s
        assert reconcile_agent_liveness()["fired"] == 0
        assert self._fire_count() == 0

    def test_auto_resolves_when_agent_reports_again(self):
        from django.utils import timezone
        from apps.agents.liveness import reconcile_agent_liveness
        from apps.alerts.models import AlertEvent
        a = self._agent(secs_ago=600)
        reconcile_agent_liveness()
        assert self._fire_count() == 1
        a.last_seen = timezone.now()      # agent resumes reporting
        a.save(update_fields=["last_seen"])
        res = reconcile_agent_liveness()
        assert res["resolved"] == 1 and self._fire_count() == 0
        ev = AlertEvent.objects.get(labels__alert_type="agent_offline")
        assert ev.state == AlertEvent.State.RESOLVED and ev.resolved_at is not None

    def test_debounce_no_duplicate_while_down(self):
        from apps.agents.liveness import reconcile_agent_liveness
        from apps.alerts.models import AlertEvent
        self._agent(secs_ago=600)
        reconcile_agent_liveness()
        reconcile_agent_liveness()  # still down → must NOT re-fire
        reconcile_agent_liveness()
        assert AlertEvent.objects.filter(labels__alert_type="agent_offline").count() == 1
        assert self._fire_count() == 1

    def test_threshold_is_configurable_per_agent(self):
        from apps.agents.liveness import reconcile_agent_liveness
        # 120s silent: fires under a tight 60s per-agent override...
        self._agent(secs_ago=120, offline_threshold_seconds=60)
        assert reconcile_agent_liveness()["fired"] == 1

    def test_default_threshold_not_tripped_by_short_blip(self):
        from apps.agents.liveness import reconcile_agent_liveness
        # ...but 120s silent does NOT fire under the 300s global default.
        self._agent(secs_ago=120)
        assert reconcile_agent_liveness()["fired"] == 0

    def test_suppressed_agent_does_not_fire_and_resolves(self):
        from apps.agents.liveness import reconcile_agent_liveness
        a = self._agent(secs_ago=600)
        reconcile_agent_liveness()
        assert self._fire_count() == 1
        a.liveness_alerts_enabled = False  # e.g. the napping lab box
        a.save(update_fields=["liveness_alerts_enabled"])
        res = reconcile_agent_liveness()
        assert res["resolved"] == 1 and self._fire_count() == 0
        # A suppressed agent never fires in the first place either.
        Agent.objects.filter(id=a.id).update(liveness_alerts_enabled=False)
        assert reconcile_agent_liveness()["fired"] == 0

    def test_revoked_agent_ignored(self):
        from apps.agents.liveness import reconcile_agent_liveness
        self._agent(secs_ago=600, status=Agent.Status.REVOKED)
        assert reconcile_agent_liveness()["fired"] == 0

    def test_never_reported_new_agent_has_grace(self):
        # last_seen=None but freshly created → grace (uses created_at), no alert.
        from apps.agents.liveness import reconcile_agent_liveness
        self._agent(secs_ago=None)
        assert reconcile_agent_liveness()["fired"] == 0

    def test_liveness_patch_action_sets_override(self, auth_client):
        a = self._agent(secs_ago=30)
        r = auth_client.patch(f"/api/servers/{a.id}/liveness/",
                              {"offline_threshold_seconds": 120, "liveness_alerts_enabled": False},
                              format="json")
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.offline_threshold_seconds == 120 and a.liveness_alerts_enabled is False


# ── Agent management ────────────────────────────────────────────────────────────

class TestAgentManagement:
    def test_list_requires_auth(self, api_client):
        assert api_client.get("/api/agents/").status_code == 401

    def test_revoke_soft_deletes(self, auth_client):
        a = Agent.objects.create(hostname="srv-x", cert_serial="s")
        assert auth_client.delete(f"/api/agents/{a.id}/").status_code == 204
        a.refresh_from_db()
        assert a.status == Agent.Status.REVOKED  # retained, not hard-deleted

    def test_download_info(self, auth_client):
        body = auth_client.get("/api/agents/download/").json()
        assert "windows-amd64" in body["platforms"] and "install_linux" in body


# ── Update-script serve routes (public, top-level /agent/update[.ps1]) ────────────

class TestUpdateScriptServe:
    def _seed(self, settings, tmp_path):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "update-agent.sh").write_text("#!/usr/bin/env bash\necho linux-updater\n")
        (scripts / "Update-Agent.ps1").write_text("# windows updater\nWrite-Host hi\n")
        settings.AGENT_DIR = str(tmp_path)

    def test_serves_linux_updater_as_text(self, api_client, settings, tmp_path):
        self._seed(settings, tmp_path)
        resp = api_client.get("/agent/update")  # public, no auth
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/plain")
        assert b"linux-updater" in resp.getvalue()

    def test_serves_windows_updater_as_text(self, api_client, settings, tmp_path):
        self._seed(settings, tmp_path)
        resp = api_client.get("/agent/update.ps1")  # public, no auth
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/plain")
        assert b"windows updater" in resp.getvalue()

    def test_missing_script_is_404(self, api_client, settings, tmp_path):
        settings.AGENT_DIR = str(tmp_path)  # no scripts/ dir → file absent
        assert api_client.get("/agent/update").status_code == 404


# ── Metric point building (pure) ────────────────────────────────────────────────

class TestBuildPoints:
    def test_translates_all_measurements(self):
        metrics = {
            "cpu": [{"core": "cpu0", "usage_pct": 40, "iowait": 2}],
            "load": {"load1": 0.5, "load5": 0.4, "load15": 0.3},
            "memory": {"total_bytes": 100, "used_bytes": 60, "usage_pct": 60},
            "disk": [{"mount": "/", "device": "sda1", "total_bytes": 10, "used_bytes": 5, "usage_pct": 50}],
            "network": [{"interface": "eth0", "rx_bytes": 10, "tx_bytes": 20, "rx_bps": 100}],
        }
        pts = build_points(42, "srv", metrics)
        by_m = {p["measurement"]: p for p in pts}
        assert set(by_m) == {"cpu", "load", "memory", "disk", "interface"}
        assert by_m["cpu"]["tags"] == {"device_id": "42", "hostname": "srv", "core": "cpu0"}
        assert by_m["cpu"]["fields"]["usage_pct"] == 40.0
        assert by_m["disk"]["tags"]["mount"] == "/"
        assert by_m["interface"]["fields"]["rx_bps"] == 100.0

    def test_drops_empty_and_nonnumeric(self):
        pts = build_points(1, "h", {"memory": {"total_bytes": "n/a"}, "cpu": []})
        assert pts == []  # non-numeric dropped → empty fields → no point


# ── Site assignment at enrollment + reassignment on the server detail ─────────

class TestServerSiteAssignment:
    def test_enroll_assigns_device_site_from_token(self, api_client, fake_pki):
        from apps.devices.models import Site
        site = Site.objects.create(name="DC-enroll")
        t = _token(max_uses=1, site=site)
        resp = _enroll(api_client, t.token, hostname="srv-a")
        assert resp.status_code == 201, resp.content
        agent = Agent.objects.get(id=resp.json()["agent_id"])
        assert agent.device is not None and agent.device.site_id == site.id

    def test_enroll_without_site_leaves_device_unassigned(self, api_client, fake_pki):
        t = _token(max_uses=1)  # no site
        resp = _enroll(api_client, t.token, hostname="srv-b")
        assert resp.status_code == 201, resp.content
        agent = Agent.objects.get(id=resp.json()["agent_id"])
        assert agent.device is not None and agent.device.site_id is None

    def test_change_site_reassigns_and_audits(self, api_client, auth_client, fake_pki):
        from apps.core.models import AuditLog
        from apps.devices.models import Site
        dest = Site.objects.create(name="DC-dest")
        t = _token(max_uses=1)
        agent_id = _enroll(api_client, t.token, hostname="srv-c").json()["agent_id"]

        resp = auth_client.post(f"/api/servers/{agent_id}/site/", {"site_id": dest.id}, format="json")
        assert resp.status_code == 200, resp.content
        assert resp.json()["site"]["id"] == dest.id
        agent = Agent.objects.get(id=agent_id)
        assert agent.device.site_id == dest.id
        assert AuditLog.objects.filter(
            event_type=AuditLog.EventType.AGENT_SITE_CHANGED).exists()

    def test_change_site_unassign_with_null(self, api_client, auth_client, fake_pki):
        from apps.devices.models import Site
        site = Site.objects.create(name="DC-start")
        t = _token(max_uses=1, site=site)
        agent_id = _enroll(api_client, t.token, hostname="srv-d").json()["agent_id"]

        resp = auth_client.post(f"/api/servers/{agent_id}/site/", {"site_id": None}, format="json")
        assert resp.status_code == 200, resp.content
        assert resp.json()["site"] is None
        assert Agent.objects.get(id=agent_id).device.site_id is None

    def test_change_site_requires_capability(self, api_client, viewer_client, fake_pki):
        from apps.devices.models import Site
        dest = Site.objects.create(name="DC-noauth")
        t = _token(max_uses=1)
        agent_id = _enroll(api_client, t.token, hostname="srv-e").json()["agent_id"]
        resp = viewer_client.post(f"/api/servers/{agent_id}/site/", {"site_id": dest.id}, format="json")
        assert resp.status_code == 403, resp.content

    def test_change_site_no_linked_device_is_400(self, auth_client):
        from apps.devices.models import Site
        dest = Site.objects.create(name="DC-nodev")
        agent = Agent.objects.create(hostname="srv-nodev", status=Agent.Status.ACTIVE)
        resp = auth_client.post(f"/api/servers/{agent.id}/site/", {"site_id": dest.id}, format="json")
        assert resp.status_code == 400, resp.content


# ── Agent version refresh from metrics payload (API-side; agent unchanged) ────

class TestAgentVersionRefresh:
    _HDR = dict(HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")

    def _agent(self, version="1.0.0"):
        return Agent.objects.create(hostname="srv-ver", cert_serial="ab:cd:ef:01", version=version)

    def test_metrics_updates_version(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(version="1.0.0")
        r = api_client.post(f"/api/agents/{a.id}/metrics/",
                            {"version": "1.2.3", "metrics": {}}, format="json", **self._HDR)
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.version == "1.2.3"

    def test_metrics_omitting_version_keeps_existing(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(version="1.2.3")
        r = api_client.post(f"/api/agents/{a.id}/metrics/",
                            {"metrics": {}}, format="json", **self._HDR)
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.version == "1.2.3"  # not blanked

    def test_metrics_empty_version_doesnt_blank(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(version="1.2.3")
        r = api_client.post(f"/api/agents/{a.id}/metrics/",
                            {"version": "  ", "metrics": {}}, format="json", **self._HDR)
        assert r.status_code == 200, r.content
        a.refresh_from_db()
        assert a.version == "1.2.3"

    def test_updated_version_surfaces_via_serializer(self, api_client, auth_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = self._agent(version="1.0.0")
        api_client.post(f"/api/agents/{a.id}/metrics/",
                        {"version": "2.0.0", "metrics": {}}, format="json", **self._HDR)
        body = auth_client.get(f"/api/servers/{a.id}/").json()
        assert body["agent_version"] == "2.0.0"


# ── Servers list filtered by site (?site=) for the site-detail Servers tab ────

class TestServerSiteFilter:
    def _server_in_site(self, site, host, ip):
        from apps.devices.models import Device
        dev = Device.objects.create(hostname=host, ip_address=ip,
                                    platform=Device.Platform.IOS_XE, site=site)
        return Agent.objects.create(hostname=host, device=dev, status=Agent.Status.ACTIVE)

    @staticmethod
    def _rows(resp):
        body = resp.json()
        return body["results"] if isinstance(body, dict) else body

    def test_servers_filtered_by_site(self, auth_client):
        from apps.devices.models import Site
        s1 = Site.objects.create(name="SF-1")
        s2 = Site.objects.create(name="SF-2")
        self._server_in_site(s1, "srv-a", "10.0.0.1")
        self._server_in_site(s1, "srv-b", "10.0.0.2")
        self._server_in_site(s2, "srv-c", "10.0.0.3")
        Agent.objects.create(hostname="srv-nodev", status=Agent.Status.ACTIVE)  # no device → no site

        resp = auth_client.get(f"/api/servers/?site={s1.pk}")
        assert resp.status_code == 200, resp.content
        hosts = {r["hostname"] for r in self._rows(resp)}
        assert hosts == {"srv-a", "srv-b"}

    def test_empty_site_returns_empty_not_error(self, auth_client):
        from apps.devices.models import Site
        s = Site.objects.create(name="SF-empty")
        resp = auth_client.get(f"/api/servers/?site={s.pk}")
        assert resp.status_code == 200, resp.content
        assert self._rows(resp) == []

    def test_no_site_param_lists_all(self, auth_client):
        from apps.devices.models import Site
        s1 = Site.objects.create(name="SF-all")
        self._server_in_site(s1, "srv-x", "10.0.1.1")
        Agent.objects.create(hostname="srv-y", status=Agent.Status.ACTIVE)
        resp = auth_client.get("/api/servers/")
        assert resp.status_code == 200, resp.content
        hosts = {r["hostname"] for r in self._rows(resp)}
        assert {"srv-x", "srv-y"}.issubset(hosts)


# ── Enrollment server_url: echo the request host, never localhost ─────────────

class TestEnrollServerUrl:
    def test_response_echoes_request_host_not_localhost(self, api_client, fake_pki, settings):
        settings.AGENT_SERVER_URL = ""  # request-derived path
        settings.ALLOWED_HOSTS = ["netpulse.example.com", "testserver", "localhost", "127.0.0.1"]
        t = _token(max_uses=1)
        resp = api_client.post(
            "/api/agents/enroll/",
            {"enrollment_token": t.token, "hostname": "win-1", "os": "windows",
             "arch": "amd64", "version": "1.0.0", "csr": CSR},
            format="json", secure=True, HTTP_HOST="netpulse.example.com")
        assert resp.status_code == 201, resp.content
        url = resp.json()["server_url"]
        assert url == "https://netpulse.example.com", url
        assert "localhost" not in url

    def test_explicit_setting_overrides_request_host(self, api_client, fake_pki, settings):
        settings.AGENT_SERVER_URL = "https://pinned.example.com"
        t = _token(max_uses=1)
        resp = _enroll(api_client, t.token, hostname="win-2")
        assert resp.status_code == 201, resp.content
        assert resp.json()["server_url"] == "https://pinned.example.com"

    def test_colocated_enroll_still_works(self, api_client, fake_pki, settings):
        settings.AGENT_SERVER_URL = ""
        t = _token(max_uses=1)
        resp = _enroll(api_client, t.token, hostname="win-3")  # default Host=testserver
        assert resp.status_code == 201, resp.content
        url = resp.json()["server_url"]
        assert url and "localhost" not in url  # request-derived, reachable


# ── Cache-Control: no-store on secret-bearing responses ───────────────────────

class TestNoStoreHeaders:
    def test_token_generation_is_no_store(self, auth_client):
        resp = auth_client.post("/api/agents/tokens/",
                                {"description": "x", "max_uses": 1}, format="json")
        assert resp.status_code == 201, resp.content
        assert resp.get("Cache-Control") == "no-store"

    def test_enroll_response_is_no_store(self, api_client, fake_pki):
        t = _token(max_uses=1)
        resp = _enroll(api_client, t.token, hostname="nostore-host")
        assert resp.status_code == 201, resp.content
        assert resp.get("Cache-Control") == "no-store"


# ── Stage A: desired-config view/edit + pull delivery via metrics response ────

class TestAgentDesiredConfig:
    def test_get_returns_effective_defaults(self, auth_client):
        a = Agent.objects.create(hostname="cfg-1", status=Agent.Status.ACTIVE)
        resp = auth_client.get(f"/api/servers/{a.id}/config/")
        assert resp.status_code == 200, resp.content
        cfg = resp.json()
        assert cfg["collection"]["cpu"] is True
        assert cfg["interval_seconds"] == 30
        assert cfg["disk"] == {"exclude_mounts": [], "include_mounts": []}

    def test_patch_requires_agent_edit(self, viewer_client):
        a = Agent.objects.create(hostname="cfg-2", status=Agent.Status.ACTIVE)
        resp = viewer_client.patch(f"/api/servers/{a.id}/config/",
                                   {"interval_seconds": 60}, format="json")
        assert resp.status_code == 403, resp.content

    def test_patch_persists_and_audits(self, auth_client):
        from apps.core.models import AuditLog
        a = Agent.objects.create(hostname="cfg-3", status=Agent.Status.ACTIVE)
        resp = auth_client.patch(
            f"/api/servers/{a.id}/config/",
            {"collection": {"network": False}, "interval_seconds": 120,
             "disk": {"exclude_mounts": ["D:"]}}, format="json")
        assert resp.status_code == 200, resp.content
        cfg = resp.json()
        assert cfg["collection"]["network"] is False
        assert cfg["collection"]["cpu"] is True  # untouched keys keep defaults
        assert cfg["interval_seconds"] == 120
        assert cfg["disk"]["exclude_mounts"] == ["D:"]
        a.refresh_from_db()
        assert a.desired_config["disk"]["exclude_mounts"] == ["D:"]
        assert AuditLog.objects.filter(
            event_type=AuditLog.EventType.AGENT_CONFIG_CHANGED).exists()

    def test_patch_rejects_bad_input(self, auth_client):
        a = Agent.objects.create(hostname="cfg-4", status=Agent.Status.ACTIVE)
        # interval below the floor
        assert auth_client.patch(f"/api/servers/{a.id}/config/",
                                 {"interval_seconds": 1}, format="json").status_code == 400
        # unknown collection key
        assert auth_client.patch(f"/api/servers/{a.id}/config/",
                                 {"collection": {"bogus": True}}, format="json").status_code == 400

    def test_metrics_response_carries_desired_config(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.views.write_agent_metrics", lambda *a, **k: 0)
        a = Agent.objects.create(hostname="cfg-5", cert_serial="ab:cd:ef:01",
                                 status=Agent.Status.ACTIVE,
                                 desired_config={"disk": {"exclude_mounts": ["D:"]}})
        hdr = dict(HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")
        r = api_client.post(f"/api/agents/{a.id}/metrics/", {"metrics": {}}, format="json", **hdr)
        assert r.status_code == 200, r.content
        dc = r.json()["desired_config"]
        assert dc["disk"]["exclude_mounts"] == ["D:"]
        assert dc["collection"]["cpu"] is True  # merged with defaults


# ── Stage 1 log forwarding: relay endpoint + allowlist (security profile) ──────

class TestLogForwarding:
    _HDR = dict(HTTP_X_AGENT_VERIFIED="SUCCESS", HTTP_X_AGENT_CERT_SERIAL="ABCDEF01")

    def test_logs_endpoint_relays_to_publisher(self, api_client, monkeypatch):
        captured = {}

        def fake_publish(source, host, lines):
            captured.update(source=source, host=host, lines=list(lines))
            return len(list(lines))
        monkeypatch.setattr("apps.agents.log_publish.publish_log_lines", fake_publish)
        a = Agent.objects.create(hostname="loghost", cert_serial="ab:cd:ef:01",
                                 status=Agent.Status.ACTIVE)
        r = api_client.post(
            f"/api/agents/{a.id}/logs/",
            {"source": "auth", "lines": ["sshd[1]: Failed password for root from 10.0.0.9",
                                         "sudo: pam_unix session opened"]},
            format="json", **self._HDR)
        assert r.status_code == 200, r.content
        assert r.json()["published"] == 2
        assert captured["source"] == "auth" and captured["host"] == "loghost"
        assert len(captured["lines"]) == 2

    def test_logs_endpoint_rejects_unknown_source(self, api_client, monkeypatch):
        monkeypatch.setattr("apps.agents.log_publish.publish_log_lines", lambda *a, **k: 0)
        a = Agent.objects.create(hostname="loghost2", cert_serial="ab:cd:ef:01",
                                 status=Agent.Status.ACTIVE)
        r = api_client.post(f"/api/agents/{a.id}/logs/",
                            {"source": "/etc/passwd", "lines": ["x"]}, format="json", **self._HDR)
        assert r.status_code == 400, r.content

    def test_logs_endpoint_requires_mtls(self, api_client):
        a = Agent.objects.create(hostname="loghost3", cert_serial="ab:cd:ef:01",
                                 status=Agent.Status.ACTIVE)
        # No mTLS headers → 403 (same gate as metrics).
        assert api_client.post(f"/api/agents/{a.id}/logs/",
                               {"source": "auth", "lines": ["x"]}, format="json").status_code == 403

    def test_config_default_security_profile_on(self, auth_client):
        a = Agent.objects.create(hostname="loghost4", status=Agent.Status.ACTIVE)
        cfg = auth_client.get(f"/api/servers/{a.id}/config/").json()
        assert cfg["logs"]["security_profile"] is True
        assert cfg["logs"]["additional_paths"] == []

    def test_config_allowlist_rejects_bad_path(self, auth_client):
        a = Agent.objects.create(hostname="loghost5", status=Agent.Status.ACTIVE)
        for bad in ("/etc/shadow", "/root/.ssh/id_rsa", "/var/log/../etc/passwd",
                    "/var/log/server.key", "/home/u/app.log"):
            r = auth_client.patch(f"/api/servers/{a.id}/config/",
                                  {"logs": {"additional_paths": [bad]}}, format="json")
            assert r.status_code == 400, f"{bad} should be rejected, got {r.status_code}"

    def test_config_allowlist_accepts_var_log(self, auth_client):
        a = Agent.objects.create(hostname="loghost6", status=Agent.Status.ACTIVE)
        r = auth_client.patch(f"/api/servers/{a.id}/config/",
                              {"logs": {"security_profile": False,
                                        "additional_paths": ["/var/log/myapp/app.log"]}},
                              format="json")
        assert r.status_code == 200, r.content
        assert r.json()["logs"]["additional_paths"] == ["/var/log/myapp/app.log"]
        assert r.json()["logs"]["security_profile"] is False
