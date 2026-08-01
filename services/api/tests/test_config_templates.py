"""Tests for editable config-push templates (apps.config_templates)."""

import pytest

from apps.config_templates.models import ConfigPushTemplate
from apps.config_templates.render import (
    detect_variables,
    is_sensitive,
    mask_sensitive_output,
    render_template,
    render_to_lines,
)
from apps.credentials.models import CredentialProfile
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def ssh_profile():
    return CredentialProfile.objects.create(
        name="ct-ssh", ssh_enabled=True, ssh_username="netadmin", vault_path="x")


@pytest.fixture
def aoscx_device(ssh_profile):
    return Device.objects.create(
        hostname="site1-idf3-asw-01", ip_address="192.0.2.20", management_ip="192.0.2.20",
        platform="aos_cx", vendor="HPE", status="active", credential_profile=ssh_profile)


# ── Seeding ──────────────────────────────────────────────────────────────────

class TestSeed:
    def test_builtins_seeded_by_migration(self):
        names = set(ConfigPushTemplate.objects.filter(builtin=True).values_list("name", flat=True))
        assert {"AOS-CX SNMP v3", "AOS-CX Syslog", "AOS-CX NTP", "AOS-CX Banner",
                "Cisco IOS SNMP v3", "Cisco IOS Syslog"} <= names

    def test_reseed_is_idempotent(self):
        from apps.config_templates.defaults import seed_builtin_templates
        before = ConfigPushTemplate.objects.count()
        created = seed_builtin_templates(ConfigPushTemplate)
        assert created == 0
        assert ConfigPushTemplate.objects.count() == before


# ── Render helpers ───────────────────────────────────────────────────────────

class TestRender:
    def test_detect_variables_excludes_auto(self):
        content = "snmpv3 user {{ snmp_user }} {{ device.hostname }} {{ settings.syslog_server }}"
        assert detect_variables(content) == ["snmp_user"]

    def test_is_sensitive(self):
        assert is_sensitive("snmp_auth_pass")
        assert is_sensitive("api_key")
        assert is_sensitive("client_secret")
        assert not is_sensitive("snmp_user")

    def test_render_uses_device_and_settings(self, aoscx_device, monkeypatch):
        monkeypatch.setattr("apps.core.models.SystemSetting.get",
                            classmethod(lambda cls, k, d="": "198.51.100.250" if k == "syslog_server" else d))
        out = render_template(
            "logging {{ settings.syslog_server }} host {{ device.hostname }}",
            aoscx_device, {})
        assert out == "logging 198.51.100.250 host site1-idf3-asw-01"

    def test_render_default_filter_and_conditional(self, aoscx_device):
        content = ("logging {{ syslog_server }} severity {{ syslog_severity | default('informational') }}\n"
                   "{% if syslog_port is defined %}logging port {{ syslog_port }}{% endif %}")
        out = render_template(content, aoscx_device, {"syslog_server": "10.0.0.1"})
        assert "severity informational" in out
        assert "logging port" not in out  # syslog_port not provided

    def test_mask_sensitive_output(self):
        masked = mask_sensitive_output("auth-pass S3cret123 user bob",
                                       {"snmp_auth_pass": "S3cret123", "snmp_user": "bob"})
        assert "S3cret123" not in masked
        assert "bob" in masked  # non-sensitive left intact

    def test_render_to_lines_strips_comments_and_blanks(self):
        rendered = "# comment\nsnmp-server a\n\n  \nlogging b"
        assert render_to_lines(rendered) == ["snmp-server a", "logging b"]


# ── CRUD / permissions ───────────────────────────────────────────────────────

class TestCrud:
    def test_list_requires_admin(self, viewer_client):
        assert viewer_client.get("/api/config-templates/").status_code == 403

    def test_admin_can_list(self, admin_client):
        resp = admin_client.get("/api/config-templates/")
        assert resp.status_code == 200

    def test_create_strips_sensitive_default_from_db(self, admin_client):
        resp = admin_client.post("/api/config-templates/", {
            "name": "Custom SNMP", "category": "snmp", "platform": "aos_cx",
            "template_content": "snmpv3 user {{ snmp_user }} auth-pass {{ snmp_auth_pass }}",
            "variables": {"snmp_user": "svc-snmp", "snmp_auth_pass": "topsecret"},
        }, format="json")
        assert resp.status_code == 201, resp.content
        obj = ConfigPushTemplate.objects.get(name="Custom SNMP")
        # Secret value must never be persisted to the DB.
        assert "snmp_auth_pass" not in obj.variables
        assert obj.variables.get("snmp_user") == "svc-snmp"
        # And the secret is masked (empty) in the API response.
        assert resp.json()["variables"].get("snmp_auth_pass", "") == ""

    def test_detected_variables_flags_sensitive(self, admin_client):
        resp = admin_client.post("/api/config-templates/", {
            "name": "Detect", "category": "snmp",
            "template_content": "user {{ snmp_user }} pass {{ snmp_auth_pass }}",
        }, format="json")
        detected = {d["name"]: d["sensitive"] for d in resp.json()["detected_variables"]}
        assert detected == {"snmp_user": False, "snmp_auth_pass": True}

    def test_builtin_cannot_be_deleted(self, admin_client):
        builtin = ConfigPushTemplate.objects.filter(builtin=True).first()
        resp = admin_client.delete(f"/api/config-templates/{builtin.id}/")
        assert resp.status_code == 403
        assert ConfigPushTemplate.objects.filter(id=builtin.id).exists()

    def test_custom_can_be_deleted(self, admin_client):
        obj = ConfigPushTemplate.objects.create(name="tmp", category="other",
                                                template_content="x")
        assert admin_client.delete(f"/api/config-templates/{obj.id}/").status_code == 204


# ── Preview ──────────────────────────────────────────────────────────────────

class TestPreview:
    def test_preview_renders_and_masks(self, admin_client, aoscx_device):
        tmpl = ConfigPushTemplate.objects.create(
            name="prev", category="snmp", platform="aos_cx",
            template_content="snmpv3 user {{ snmp_user }} auth-pass {{ snmp_auth_pass }}")
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/preview/", {
            "device_id": aoscx_device.id,
            "variables": {"snmp_user": "svc-snmp", "snmp_auth_pass": "S3cret"},
        }, format="json")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["device"] == "site1-idf3-asw-01"
        assert "svc-snmp" in body["rendered"]
        assert "S3cret" not in body["rendered"]  # secret masked

    def test_preview_bad_template_returns_400(self, admin_client, aoscx_device):
        tmpl = ConfigPushTemplate.objects.create(
            name="bad", category="other", template_content="{{ unclosed ")
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/preview/",
                                 {"device_id": aoscx_device.id}, format="json")
        assert resp.status_code == 400


# ── Push ─────────────────────────────────────────────────────────────────────

class TestPush:
    def _template(self):
        return ConfigPushTemplate.objects.create(
            name="push-snmp", category="snmp", platform="aos_cx",
            template_content="snmpv3 user {{ snmp_user }}")

    def test_push_blocked_when_disabled(self, admin_client, aoscx_device, settings):
        settings.ALLOW_CONFIG_PUSH = False
        tmpl = self._template()
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/",
                                 {"device_ids": [aoscx_device.id]}, format="json")
        assert resp.status_code == 403
        assert resp.json()["success"] is False
        # The blocked attempt is still audited.
        from apps.core.models import AuditLog
        assert AuditLog.objects.filter(event_type=AuditLog.EventType.CONFIG_PUSHED).exists()

    def test_push_success(self, admin_client, aoscx_device, settings, monkeypatch):
        settings.ALLOW_CONFIG_PUSH = True
        captured = {}

        class FakeConn:
            def send_config_set(self, lines, **kwargs):
                captured["lines"] = list(lines)
                return "applied"
            def disconnect(self):
                captured["disconnected"] = True

        monkeypatch.setattr("netmiko.ConnectHandler", lambda **k: FakeConn())
        tmpl = self._template()
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/", {
            "device_ids": [aoscx_device.id], "variables": {"snmp_user": "svc-snmp"},
        }, format="json")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["success"] is True
        assert body["succeeded"] == 1 and body["total"] == 1
        assert body["results"][0]["success"] is True
        assert captured["lines"] == ["snmpv3 user svc-snmp"]

    # ── Junos: private candidate + explicit commit ────────────────────────────
    # send_config_set only LOADS the Junos candidate; without a commit the push
    # silently no-ops (confirmed live on the vSRX). Other platforms apply lines
    # immediately — test_push_success's FakeConn has NO commit/config-mode
    # methods, so any commit attempt on a non-Junos push would fail that test.

    class _FakeJunosConn:
        def __init__(self, commit_fails=False):
            self.commit_fails = commit_fails
            self.calls = []
            self.send_kwargs = None
            self._in_cfg = False

        def send_config_set(self, lines, **kw):
            self.calls.append(("load", list(lines)))
            self.send_kwargs = kw
            if kw.get("exit_config_mode") is False:
                self._in_cfg = True
            return "loaded"

        def commit(self, **kw):
            self.calls.append(("commit", kw))
            if self.commit_fails:
                raise RuntimeError("error: configuration check-out failed")
            return "commit complete"

        def send_command(self, cmd, **kw):
            self.calls.append(("cmd", cmd))
            return ""

        def check_config_mode(self):
            return self._in_cfg

        def exit_config_mode(self):
            self.calls.append(("exit-cfg",))
            self._in_cfg = False

        def disconnect(self):
            self.calls.append(("disconnect",))

    def _junos_setup(self, ssh_profile, monkeypatch, conn):
        dev = Device.objects.create(
            hostname="vsrx-1", ip_address="192.0.2.60", platform="junos",
            vendor="juniper", status="active", credential_profile=ssh_profile)
        monkeypatch.setattr("netmiko.ConnectHandler", lambda **k: conn)
        tmpl = ConfigPushTemplate.objects.create(
            name="junos-snmp", category="snmp", platform="junos",
            template_content="set snmp location {{ loc }}")
        return dev, tmpl

    def test_push_junos_uses_private_mode_and_commits(self, admin_client, ssh_profile,
                                                      settings, monkeypatch):
        settings.ALLOW_CONFIG_PUSH = True
        conn = self._FakeJunosConn()
        dev, tmpl = self._junos_setup(ssh_profile, monkeypatch, conn)
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/", {
            "device_ids": [dev.id], "variables": {"loc": "LAB"}}, format="json")
        assert resp.status_code == 200, resp.content
        assert resp.json()["results"][0]["success"] is True
        # isolated private candidate, load stays in config mode for the commit
        assert conn.send_kwargs["config_mode_command"] == "configure private"
        assert conn.send_kwargs["exit_config_mode"] is False
        ops = [c[0] for c in conn.calls]
        assert "commit" in ops                          # the actual fix
        assert ops.index("load") < ops.index("commit")
        assert "exit-cfg" in ops and "disconnect" in ops
        commit_kw = next(c[1] for c in conn.calls if c[0] == "commit")
        assert commit_kw.get("comment") == "spane config push"

    def test_push_junos_commit_failure_is_real_failure(self, admin_client, ssh_profile,
                                                       settings, monkeypatch):
        settings.ALLOW_CONFIG_PUSH = True
        conn = self._FakeJunosConn(commit_fails=True)
        dev, tmpl = self._junos_setup(ssh_profile, monkeypatch, conn)
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/", {
            "device_ids": [dev.id], "variables": {"loc": "LAB"}}, format="json")
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["success"] is False               # NOT a silent false success
        assert "commit failed" in result["error"] or "push/commit failed" in result["error"]
        # candidate discarded + config mode exited, never left lingering
        assert ("cmd", "rollback 0") in conn.calls
        assert ("exit-cfg",) in conn.calls
        # audit records the failure
        from apps.core.models import AuditLog
        row = AuditLog.objects.filter(
            event_type=AuditLog.EventType.CONFIG_PUSHED).order_by("-id").first()
        assert row.success is False

    def test_push_platform_mismatch_skips_connection(self, admin_client, ssh_profile, settings, monkeypatch):
        settings.ALLOW_CONFIG_PUSH = True

        def boom(**k):  # connection must never be attempted on a mismatch
            raise AssertionError("should not connect on platform mismatch")
        monkeypatch.setattr("netmiko.ConnectHandler", boom)

        ios_dev = Device.objects.create(
            hostname="ios-rtr", ip_address="10.0.0.99", platform="ios",
            status="active", credential_profile=ssh_profile)
        tmpl = self._template()  # platform aos_cx
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/",
                                 {"device_ids": [ios_dev.id]}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "platform mismatch" in body["results"][0]["error"]

    def test_push_requires_device_ids(self, admin_client, settings):
        settings.ALLOW_CONFIG_PUSH = True
        tmpl = self._template()
        resp = admin_client.post(f"/api/config-templates/{tmpl.id}/push/", {}, format="json")
        assert resp.status_code == 400
