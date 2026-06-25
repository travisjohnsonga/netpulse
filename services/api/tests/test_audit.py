"""Audit log: log_event util, instrumentation, and the API."""
import pytest

from apps.core.audit import log_event
from apps.core.models import AuditLog
from apps.devices.models import Device

pytestmark = pytest.mark.django_db
ET = AuditLog.EventType


class TestLogEvent:
    def test_records_target_snapshot(self, user):
        d = Device.objects.create(hostname="r1", ip_address="10.0.0.1", platform="ios_xe")
        ev = log_event(ET.DEVICE_CREATED, user=user, target=d, description="x",
                       metadata={"k": "v"})
        assert ev.event_type == "device_created"
        assert ev.target_type == "Device" and ev.target_id == str(d.id)
        assert ev.target_name == "r1" and ev.username == user.username
        assert ev.metadata == {"k": "v"} and ev.success is True

    def test_never_raises(self):
        # A bad metadata value or missing target must not blow up the caller.
        ev = log_event(ET.LOGIN_FAILED, username="ghost", success=False)
        assert ev.username == "ghost" and ev.success is False

    def test_client_ip_from_forwarded_header(self, rf):
        # Spoof-resistant: with NUM_PROXIES=1, the trusted hop is the RIGHT-most
        # entry (appended by our own proxy), not the client-supplied leading one.
        # "203.0.113.9" is the forged/untrusted prefix and must NOT be recorded.
        req = rf.post("/x", HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.1")
        req.user = type("U", (), {"is_authenticated": False})()
        ev = log_event(ET.LOGIN_SUCCESS, request=req, username="a")
        assert ev.ip_address == "10.0.0.1"
        assert ev.ip_address != "203.0.113.9"


class TestInstrumentation:
    def test_login_success_and_failure_audited(self, api_client, django_user_model):
        django_user_model.objects.create_user(username="bob", password="pw-correct-123", role="admin")
        ok = api_client.post("/api/auth/token/", {"username": "bob", "password": "pw-correct-123"}, format="json")
        assert ok.status_code == 200
        assert AuditLog.objects.filter(event_type=ET.LOGIN_SUCCESS, username="bob").exists()

        bad = api_client.post("/api/auth/token/", {"username": "bob", "password": "wrong"}, format="json")
        assert bad.status_code == 401
        fail = AuditLog.objects.get(event_type=ET.LOGIN_FAILED, username="bob")
        assert fail.success is False

    def test_device_create_and_delete_audited(self, auth_client):
        resp = auth_client.post("/api/devices/", {
            "hostname": "audit-dev", "ip_address": "10.9.0.1", "platform": "ios_xe"}, format="json")
        assert resp.status_code in (200, 201)
        dev_id = resp.json()["id"]
        assert AuditLog.objects.filter(event_type=ET.DEVICE_CREATED, target_id=str(dev_id)).exists()
        auth_client.delete(f"/api/devices/{dev_id}/")
        assert AuditLog.objects.filter(event_type=ET.DEVICE_DELETED, target_id=str(dev_id)).exists()


class TestApi:
    def _seed(self, user, n_login=3, n_fail=1):
        for i in range(n_login):
            log_event(ET.LOGIN_SUCCESS, user=user, username=user.username, description="login")
        for i in range(n_fail):
            log_event(ET.LOGIN_FAILED, username="mallory", success=False)

    def test_list_and_filter(self, auth_client, user):
        self._seed(user)
        # Admin can read; filter by event_type.
        body = auth_client.get("/api/audit-log/?event_type=login_failed").json()
        assert body["count"] == 1
        assert body["results"][0]["event_type"] == "login_failed"
        assert body["results"][0]["event_label"] == "Login Failed"

    def test_search(self, auth_client, user):
        self._seed(user)
        body = auth_client.get("/api/audit-log/?search=mallory").json()
        assert body["count"] == 1

    def test_stats(self, auth_client, user):
        self._seed(user, n_login=3, n_fail=2)
        body = auth_client.get("/api/audit-log/stats/").json()
        assert body["today"] == 5
        assert body["failed_logins_24h"] == 2
        assert body["by_event_type"]["login_success"] == 3

    def test_export_csv(self, auth_client, user):
        self._seed(user)
        resp = auth_client.get("/api/audit-log/export/")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert b"Event" in resp.content and b"login_success" in resp.content

    def test_non_admin_forbidden(self, viewer_client, user):
        self._seed(user)
        assert viewer_client.get("/api/audit-log/").status_code == 403

    def test_device_audit_history_endpoint(self, auth_client):
        d = Device.objects.create(hostname="r1", ip_address="10.0.0.1", platform="ios_xe")
        log_event(ET.CONFIG_PUSHED, target=d, description="push")
        body = auth_client.get(f"/api/devices/{d.id}/audit/").json()
        assert len(body) == 1 and body[0]["event_type"] == "config_pushed"


class TestDiffUtilities:
    def test_diff_model_changes_detects_and_labels(self):
        from apps.core.audit import diff_model_changes
        before = {"os_version": "FL.10.12", "model": "6100", "updated_at": "t1"}
        after = {"os_version": "FL.10.13", "model": "6100", "updated_at": "t2"}
        changes = diff_model_changes(before, after, {"os_version": "OS Version"})
        # Only os_version changed; updated_at is skipped, model is unchanged.
        assert changes == [{"field": "os_version", "label": "OS Version",
                            "before": "FL.10.12", "after": "FL.10.13"}]

    def test_diff_titlecases_unlabelled_fields_and_handles_none(self):
        from apps.core.audit import diff_model_changes
        changes = diff_model_changes({"serial_number": None}, {"serial_number": "ABC123"})
        assert changes[0]["label"] == "Serial Number"
        assert changes[0]["before"] is None and changes[0]["after"] == "ABC123"

    def test_snapshot_device_shape(self):
        from apps.core.audit import snapshot_device
        d = Device.objects.create(hostname="snap1", ip_address="10.0.0.2",
                                  platform="aos_cx", os_version="FL.10.12")
        snap = snapshot_device(d)
        assert snap["hostname"] == "snap1" and snap["os_version"] == "FL.10.12"
        assert snap["site"] is None and snap["role"] is None


class TestFieldLevelDeviceDiff:
    def test_patch_records_field_changes(self, auth_client):
        d = Device.objects.create(hostname="diff-dev", ip_address="10.9.1.1",
                                  platform="aos_cx", os_version="FL.10.12")
        resp = auth_client.patch(f"/api/devices/{d.id}/",
                                 {"os_version": "FL.10.13", "notes": "upgraded"}, format="json")
        assert resp.status_code == 200, resp.content
        ev = AuditLog.objects.get(event_type=ET.DEVICE_UPDATED, target_id=str(d.id))
        labels = {c["label"]: (c["before"], c["after"]) for c in ev.metadata["changes"]}
        assert labels["OS Version"] == ("FL.10.12", "FL.10.13")
        assert "Notes" in labels
        assert "upgraded" not in ev.description or "Notes" in ev.description

    def test_no_op_patch_logs_nothing(self, auth_client):
        d = Device.objects.create(hostname="noop-dev", ip_address="10.9.1.2",
                                  platform="ios_xe", os_version="1.0")
        auth_client.patch(f"/api/devices/{d.id}/", {"os_version": "1.0"}, format="json")
        assert not AuditLog.objects.filter(event_type=ET.DEVICE_UPDATED, target_id=str(d.id)).exists()


class TestExtendedInstrumentation:
    def test_site_update_audited_with_diff(self, auth_client):
        from apps.devices.models import Site
        site = Site.objects.create(name="WCO2", description="old")
        resp = auth_client.patch(f"/api/sites/{site.pk}/", {"description": "new"}, format="json")
        assert resp.status_code == 200, resp.content
        ev = AuditLog.objects.get(event_type=ET.SITE_UPDATED, target_id=str(site.pk))
        assert any(c["label"] == "Description" for c in ev.metadata["changes"])

    def test_site_create_and_delete_audited(self, auth_client):
        created = auth_client.post("/api/sites/", {"name": "DC-Audit"}, format="json").json()
        assert AuditLog.objects.filter(event_type=ET.SITE_CREATED, target_id=str(created["id"])).exists()
        auth_client.delete(f"/api/sites/{created['id']}/")
        assert AuditLog.objects.filter(event_type=ET.SITE_DELETED, target_id=str(created["id"])).exists()

    def test_credential_create_audited_without_secrets(self, auth_client):
        resp = auth_client.post("/api/credentials/", {
            "name": "Cisco-Audit", "ssh_enabled": True, "ssh_username": "admin",
            "ssh_auth_method": "password", "ssh_password": "s3cret-pw",
        }, format="json")
        assert resp.status_code == 201, resp.content
        ev = AuditLog.objects.get(event_type=ET.CREDENTIAL_CREATED)
        assert "Cisco-Audit" in ev.description
        # The secret must never appear anywhere on the audit record.
        assert "s3cret-pw" not in ev.description
        assert "s3cret-pw" not in str(ev.metadata)
