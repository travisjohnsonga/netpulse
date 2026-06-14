"""
Reports: Compliance Summary + Daily Operations builders, renderers, endpoints,
storage/download, and scheduling.
"""
import pytest
from django.utils import timezone

from apps.configbackup.models import DeviceConfig
from apps.core.models import AuditLog
from apps.devices.models import Device, DeviceRole, Site
from apps.reports import daily_ops as dops
from apps.reports.compliance_summary import build_compliance_summary
from apps.reports.generate import generate
from apps.reports.models import GeneratedReport, ReportSchedule, ReportType

pytestmark = pytest.mark.django_db


@pytest.fixture
def fleet():
    site = Site.objects.create(name="WCO2")
    role = DeviceRole.objects.create(name="Access Switch")
    d1 = Device.objects.create(hostname="sw-1", ip_address="10.0.0.1", platform="aos_cx",
                               site=site, role=role)
    d2 = Device.objects.create(hostname="sw-2", ip_address="10.0.0.2", platform="ios",
                               site=site, role=role)
    return {"site": site, "role": role, "devices": [d1, d2]}


def _cfg(device, **kw):
    kw.setdefault("content", "x")
    kw.setdefault("content_hash", "z" * 8)
    return DeviceConfig.objects.create(
        device=device, config_type=DeviceConfig.ConfigType.RUNNING,
        collected_at=timezone.now(), **kw)


# ── Compliance Summary builder ───────────────────────────────────────────────

class TestComplianceSummary:
    def test_structure_and_grouping(self, fleet):
        # one device has an unsaved config → shows in startup_mismatch + failing
        _cfg(fleet["devices"][0], startup_match=False, startup_diff="+ vlan 55\n+ vlan 56",
             startup_checked_at=timezone.now())
        data = build_compliance_summary()
        assert data["summary"]["total_devices"] == 2
        assert {r["site"] for r in data["by_site"]} == {"WCO2"}
        assert {r["role"] for r in data["by_role"]} == {"Access Switch"}
        assert {r["platform"] for r in data["by_platform"]} == {"aos_cx", "ios"}
        assert any(m["hostname"] == "sw-1" for m in data["startup_mismatch"])
        assert data["startup_mismatch"][0]["unsaved_lines"] == 2

    def test_group_by_subset(self, fleet):
        data = build_compliance_summary(group_by=["platform"])
        assert "by_platform" in data
        assert "by_site" not in data


# ── Daily Ops builder ────────────────────────────────────────────────────────

class TestDailyOps:
    def test_sections_present(self, fleet):
        data = dops.build_daily_ops()
        for key in ("security_events", "spane_access_events", "device_availability",
                    "compliance_events", "service_checks", "config_changes",
                    "collection_health", "agent_health", "alerts_summary"):
            assert key in data

    def test_spane_access_login_failures_counted(self, fleet, monkeypatch):
        # spane's OWN login audit now lives in the spane_access_events section.
        # Stub OpenSearch (device events) so the result is independent of live syslog.
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        log = AuditLog.objects.create(event_type=AuditLog.EventType.LOGIN_FAILED,
                                      username="admin", ip_address="10.150.1.45")
        AuditLog.objects.filter(pk=log.pk).update(created_at=when)
        data = dops.build_daily_ops(date=when.date().isoformat())
        assert data["spane_access_events"]["total_failures"] == 1
        assert data["spane_access_events"]["unique_sources"] == 1
        # The AuditLog login must NOT show up as a device security event.
        assert data["security_events"]["total_failures"] == 0

    def test_device_security_events_from_syslog(self, fleet, monkeypatch):
        # Device auth failures come from OpenSearch syslog (netpulse-logs-*).
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        fake = {"hits": {"hits": [
            {"_source": {"@timestamp": when.replace(hour=22).isoformat(),
                         "hostname": "sw-1", "source_ip": "10.0.0.9",
                         "severity_name": "warning",
                         "message": "%SEC_LOGIN-4-LOGIN_FAILED: Login failed user=foo"}},
            {"_source": {"@timestamp": when.replace(hour=12).isoformat(),
                         "hostname": "sw-2", "source_ip": "10.0.0.9",
                         "message": "Failed password for invalid user bar"}},
        ]}}
        monkeypatch.setattr("apps.logs.views._execute", lambda body: fake)
        data = dops.build_daily_ops(date=when.date().isoformat())
        sec = data["security_events"]
        assert sec["available"] is True
        assert sec["total_failures"] == 2
        assert sec["unique_sources"] == 1  # both from 10.0.0.9
        assert sec["after_hours_failures"] == 1  # the 22:00 one
        assert sec["note"] == ""

    def test_device_security_events_degrade_gracefully(self, fleet, monkeypatch):
        def _boom(body):
            raise RuntimeError("opensearch down")
        monkeypatch.setattr("apps.logs.views._execute", _boom)
        data = dops.build_daily_ops()
        sec = data["security_events"]
        assert sec["available"] is False
        assert sec["total_failures"] == 0
        assert "TACACS" in sec["note"]

    def test_radius_success_not_counted_as_failure(self, fleet, monkeypatch):
        # The bug: "succeeded with RADIUS" was counted as a failure (matched "radius").
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        fake = {"hits": {"hits": [
            {"_source": {"@timestamp": when.replace(hour=10).isoformat(), "hostname": "sw-1",
                         "source_ip": "10.0.0.5",
                         "message": "User authentication for svc_backup succeeded with RADIUS server"}},
        ]}}
        monkeypatch.setattr("apps.logs.views._execute", lambda body: fake)
        data = dops.build_daily_ops(date=when.date().isoformat())
        assert data["security_events"]["total_failures"] == 0

    def test_device_security_grouping_and_multi_device_flag(self, fleet, monkeypatch):
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        hits = [{"_source": {"@timestamp": when.replace(hour=10, minute=m).isoformat(),
                             "hostname": f"sw-{i}", "source_ip": "10.150.0.18",
                             "message": f"%SEC_LOGIN-4-LOGIN_FAILED: Login failed [user: travis-admin] dev {i}"}}
                for i, m in enumerate((21, 22, 23), start=1)]
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": hits}})
        data = dops.build_daily_ops(date=when.date().isoformat())
        sec = data["security_events"]
        assert sec["total_failures"] == 3
        assert sec["device_count"] == 3
        assert len(sec["groups"]) == 1
        g = sec["groups"][0]
        assert g["username"] == "travis-admin" and g["count"] == 3 and g["device_count"] == 3
        assert any("3 devices" in f for f in sec["flags"])

    def test_success_after_failures_flagged(self, fleet, monkeypatch):
        # Requires REPEATED failures on the same device shortly before a success.
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        hits = [
            {"_source": {"@timestamp": when.replace(hour=10, minute=m).isoformat(),
                         "hostname": "sw-1", "source_ip": "10.0.0.7",
                         "message": "Login failed [user: travis-admin]"}}
            for m in (21, 22, 23)
        ] + [
            {"_source": {"@timestamp": when.replace(hour=10, minute=25).isoformat(),
                         "hostname": "sw-1", "source_ip": "10.0.0.7",
                         "message": "%SEC_LOGIN-5-LOGIN_SUCCESS: login successful [user: travis-admin]"}},
        ]
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": hits}})
        data = dops.build_daily_ops(date=when.date().isoformat())
        sec = data["security_events"]
        assert sec["total_failures"] == 3  # the success is not a failure
        saf = sec["success_after_failures"]
        assert len(saf) == 1
        assert saf[0]["username"] == "travis-admin" and saf[0]["fail_count"] == 3

    def test_success_after_failures_not_flagged_for_single_failure(self, fleet, monkeypatch):
        # A lone failure then a success (e.g. rejected SSH key then password OK)
        # is normal operation, NOT a brute force — must not be flagged.
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        hits = [
            {"_source": {"@timestamp": when.replace(hour=10, minute=21).isoformat(),
                         "hostname": "sw-1", "source_ip": "10.0.0.7",
                         "message": "SSH session for user travis-admin rejected due to failed "
                                    "public key validation"}},
            {"_source": {"@timestamp": when.replace(hour=10, minute=25).isoformat(),
                         "hostname": "sw-1", "source_ip": "10.0.0.7",
                         "message": "login successful [user: travis-admin]"}},
        ]
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": hits}})
        data = dops.build_daily_ops(date=when.date().isoformat())
        assert data["security_events"]["total_failures"] == 1
        assert data["security_events"]["success_after_failures"] == []

    def test_spane_admin_actions_and_after_hours(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        import datetime as _dt
        when = timezone.now() - _dt.timedelta(days=1)
        day = when.date()
        # an admin action + an after-hours login
        a = AuditLog.objects.create(event_type=AuditLog.EventType.USER_CREATED,
                                    username="admin", target_name="bob")
        ah_dt = timezone.make_aware(_dt.datetime.combine(day, _dt.time(22, 0)))
        b = AuditLog.objects.create(event_type=AuditLog.EventType.LOGIN_SUCCESS,
                                    username="ops", ip_address="10.0.0.3")
        AuditLog.objects.filter(pk=a.pk).update(created_at=ah_dt)
        AuditLog.objects.filter(pk=b.pk).update(created_at=ah_dt)
        data = dops.build_daily_ops(date=day.isoformat())
        sp = data["spane_access_events"]
        assert "successful_logins" not in sp  # routine successes removed
        assert len(sp["admin_actions"]) == 1 and sp["admin_actions"][0]["target"] == "bob"
        assert len(sp["after_hours_logins"]) == 1

    def test_compliance_state_and_trend(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        import datetime as _dt
        from apps.compliance.models import ComplianceTemplate, ComplianceTemplateResult as CTR
        d1, d2 = fleet["devices"]
        tmpl = ComplianceTemplate.objects.create(name="base", template_content="x")
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        prev_day = day - _dt.timedelta(days=1)
        today_t = timezone.make_aware(_dt.datetime.combine(day, _dt.time(8, 0)))
        prev_t = timezone.make_aware(_dt.datetime.combine(prev_day, _dt.time(8, 0)))

        def _ctr(dev, score, at):
            r = CTR.objects.create(device=dev, template=tmpl,
                                   status=CTR.Status.NON_COMPLIANT, score=score)
            CTR.objects.filter(pk=r.pk).update(checked_at=at)

        # d1 degraded 80->60 (now failing); d2 improved 60->90
        _ctr(d1, 80, prev_t); _ctr(d1, 60, today_t)
        _ctr(d2, 60, prev_t); _ctr(d2, 90, today_t)

        data = dops.build_daily_ops(date=day.isoformat())
        ce = data["compliance_events"]
        assert ce["fleet_avg_today"] == 75.0  # (60+90)/2
        assert ce["fleet_avg_prev"] == 70.0   # (80+60)/2
        assert ce["total_failing_devices"] == 1  # d1 at 60 (<70)
        assert any(r["hostname"] == "sw-1" for r in ce["failing_devices"])
        assert any(r["hostname"] == "sw-1" and r["delta"] == -20.0 for r in ce["degraded"])
        assert any(r["hostname"] == "sw-2" and r["delta"] == 30.0 for r in ce["improved"])

    def test_compliance_score_asof_when_not_checked_on_report_day(self, fleet, monkeypatch):
        # Score must still appear on a report day with no checks (as-of latest).
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        import datetime as _dt
        from apps.compliance.models import ComplianceTemplate, ComplianceTemplateResult as CTR
        d1 = fleet["devices"][0]
        tmpl = ComplianceTemplate.objects.create(name="base", template_content="x")
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        # only an OLD result, two days before the report day
        old_t = timezone.make_aware(_dt.datetime.combine(day - _dt.timedelta(days=2), _dt.time(8, 0)))
        r = CTR.objects.create(device=d1, template=tmpl, status=CTR.Status.NON_COMPLIANT, score=55)
        CTR.objects.filter(pk=r.pk).update(checked_at=old_t)
        data = dops.build_daily_ops(date=day.isoformat())
        ce = data["compliance_events"]
        assert ce["fleet_avg_today"] == 55.0   # carried forward as-of, not "no scores"
        assert ce["total_failing_devices"] == 1

    def test_alerts_critical_events(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        import datetime as _dt
        from apps.alerts.models import AlertEvent, AlertRule
        when = timezone.now() - _dt.timedelta(days=1)
        rule = AlertRule.objects.create(name="device-unreachable",
                                        severity=AlertRule.Severity.CRITICAL, condition={})
        e = AlertEvent.objects.create(rule=rule, state=AlertEvent.State.FIRING,
                                      labels={"hostname": "sw-1"},
                                      annotations={"title": "Device sw-1 unreachable"})
        AlertEvent.objects.filter(pk=e.pk).update(created_at=when)
        data = dops.build_daily_ops(date=when.date().isoformat())
        al = data["alerts_summary"]
        assert al["critical"] == 1
        assert any(c["device"] == "sw-1" and "unreachable" in c["alert"].lower()
                   for c in al["critical_events"])

    def test_service_checks_not_configured(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        data = dops.build_daily_ops()
        svc = data["service_checks"]
        assert svc["configured"] is False
        assert "Settings → Checks" in svc["note"]

    def test_service_check_failures_and_correlation(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        import datetime as _dt
        from apps.alerts.models import AlertEvent, AlertRule
        from apps.checks.models import CheckResult, ServiceCheck
        d1 = fleet["devices"][0]
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        down_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(20, 3)))
        up_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(20, 12)))

        # An outage for sw-1 (so the check failures can correlate).
        rule = AlertRule.objects.create(name="device-unreachable",
                                        severity=AlertRule.Severity.HIGH, condition={})
        e = AlertEvent.objects.create(
            rule=rule, state=AlertEvent.State.RESOLVED, resolved_at=up_at,
            labels={"source": "reachability_monitor", "device_id": d1.id, "hostname": d1.hostname},
            annotations={"title": f"Device {d1.hostname} unreachable"})
        AlertEvent.objects.filter(pk=e.pk).update(created_at=down_at)

        chk = ServiceCheck.objects.create(name="Ping sw-1", check_type="icmp",
                                          host="10.0.0.1", device=d1, site=fleet["site"])
        # three failing results inside the outage window + one pass
        for minute, st, rt in [(3, "down", 30000.0), (6, "down", 30000.0),
                               (12, "down", 30000.0), (30, "up", 5.0)]:
            CheckResult.objects.create(
                service_check=chk, status=st, response_time_ms=rt, error="timeout" if st == "down" else "",
                checked_at=timezone.make_aware(_dt.datetime.combine(day, _dt.time(20, minute))))

        data = dops.build_daily_ops(date=day.isoformat())
        svc = data["service_checks"]
        assert svc["configured"] is True
        assert svc["total_executions"] == 4
        assert svc["total_failures"] == 3
        assert svc["pass_rate"] == 25.0
        assert svc["affected_checks"] == 1
        s = svc["summaries"][0]
        assert s["check_name"] == "Ping sw-1" and s["failure_count"] == 3
        assert s["avg_duration_s"] == 30.0 and s["max_duration_s"] == 30.0
        assert s["last_error"] == "timeout"
        assert s["correlated_outage"] is not None
        assert s["correlated_outage"]["hostname"] == "sw-1"

    def test_collection_health_status_breakdown(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        from apps.configbackup.models import ConfigCollectionLog as CCL
        d1, d2 = fleet["devices"]
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        for dev, status in [(d1, CCL.Status.SUCCESS), (d1, CCL.Status.UNCHANGED),
                            (d2, CCL.Status.TIMEOUT)]:
            r = CCL.objects.create(device=dev, status=status, collected_by="scheduled")
            CCL.objects.filter(pk=r.pk).update(collected_at=when)
        data = dops.build_daily_ops(date=when.date().isoformat())
        ch = data["collection_health"]
        assert ch["total_attempts"] == 3
        assert ch["device_count"] == 2
        assert ch["successful"] == 2  # success + unchanged
        by = {s["status"]: s["count"] for s in ch["by_status"]}
        assert by == {"success": 1, "unchanged": 1, "timeout": 1}
        assert any(f["hostname"] == "sw-2" and f["error"] == "timeout" for f in ch["failed_devices"])

    def test_outages_from_alert_events(self, fleet):
        # Reconstruct outages from device-unreachable AlertEvents: a recovered
        # outage (resolved) and a still-down one (firing).
        import datetime as _dt
        from apps.alerts.models import AlertEvent, AlertRule
        d1, d2 = fleet["devices"]
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        down_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(10, 0)))
        up_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(10, 30)))
        rule = AlertRule.objects.create(name="device-unreachable",
                                        severity=AlertRule.Severity.HIGH, condition={})

        def _ev(dev, state, resolved_at=None):
            e = AlertEvent.objects.create(
                rule=rule, state=state,
                labels={"source": "reachability_monitor", "device_id": dev.id,
                        "hostname": dev.hostname},
                annotations={"title": f"Device {dev.hostname} unreachable"},
                resolved_at=resolved_at)
            AlertEvent.objects.filter(pk=e.pk).update(created_at=down_at)
            return e

        _ev(d1, AlertEvent.State.RESOLVED, resolved_at=up_at)   # recovered
        _ev(d2, AlertEvent.State.FIRING)                        # still down

        data = dops.build_daily_ops(date=day.isoformat())
        av = data["device_availability"]
        assert av["total_outages"] == 2
        downs = {o["hostname"]: o for o in av["went_down"]}
        assert downs["sw-1"]["recovered_at"] is not None
        assert downs["sw-1"]["duration_minutes"] == 30
        assert downs["sw-1"]["still_down"] is False
        assert downs["sw-2"]["still_down"] is True
        assert any(o["hostname"] == "sw-2" for o in av["still_down"])

    def test_outage_skips_reachable_again_events(self, fleet):
        # The paired "reachable again" FIRING event must not be counted as an outage.
        import datetime as _dt
        from apps.alerts.models import AlertEvent, AlertRule
        d1 = fleet["devices"][0]
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        when = timezone.make_aware(_dt.datetime.combine(day, _dt.time(11, 0)))
        rule = AlertRule.objects.create(name="device-unreachable",
                                        severity=AlertRule.Severity.INFO, condition={})
        e = AlertEvent.objects.create(
            rule=rule, state=AlertEvent.State.FIRING,
            labels={"source": "reachability_monitor", "device_id": d1.id,
                    "hostname": d1.hostname},
            annotations={"title": f"Device {d1.hostname} reachable again"})
        AlertEvent.objects.filter(pk=e.pk).update(created_at=when)
        data = dops.build_daily_ops(date=day.isoformat())
        assert data["device_availability"]["total_outages"] == 0

    def test_config_changes_listed(self, fleet):
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        c = _cfg(fleet["devices"][0], changed_from_previous=True, diff_summary="+ vlan 55")
        DeviceConfig.objects.filter(pk=c.pk).update(collected_at=when)
        data = dops.build_daily_ops(date=when.date().isoformat())
        assert any(cc["hostname"] == "sw-1" for cc in data["config_changes"])

    def test_config_change_full_diff_computed_on_the_fly(self, fleet):
        import datetime as _dt
        dev = fleet["devices"][0]
        day = (timezone.now() - _dt.timedelta(days=1)).date()
        prev_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(7, 0)))
        cur_at = timezone.make_aware(_dt.datetime.combine(day, _dt.time(19, 0)))
        p = _cfg(dev, content="hostname sw-1\nvlan 10\n")
        DeviceConfig.objects.filter(pk=p.pk).update(collected_at=prev_at, content_hash="a" * 8)
        c = _cfg(dev, changed_from_previous=True, content="hostname sw-1\nvlan 10\nvlan 55\n")
        DeviceConfig.objects.filter(pk=c.pk).update(collected_at=cur_at, content_hash="b" * 8)

        data = dops.build_daily_ops(date=day.isoformat())
        change = next(cc for cc in data["config_changes"] if cc["hostname"] == "sw-1")
        assert change["lines_added"] == 1 and change["lines_removed"] == 0
        assert "+vlan 55" in change["diff"]
        assert change["previous_backup_at"] is not None
        assert change["current_backup_at"] is not None
        assert change["site"] == "WCO2" and change["role"] == "Access Switch"
        assert "vlan 55" in change["diff_summary"]


class TestEmailContent:
    def test_daily_ops_email_summary(self, fleet):
        from apps.reports.tasks import email_content
        data = dops.build_daily_ops()
        subject, body = email_content(ReportType.DAILY_OPS, data, timezone.now())
        assert subject.startswith("spane Daily Ops Report - ")
        assert "Quick Summary:" in body
        assert "Powered by spane" in body


# ── generate() + storage ─────────────────────────────────────────────────────

class TestGenerate:
    @pytest.mark.parametrize("fmt,head", [("pdf", b"%PDF-"), ("csv", None), ("json", b"{")])
    def test_compliance_formats(self, fleet, fmt, head):
        report, content, _data = generate(ReportType.COMPLIANCE_SUMMARY, fmt, {}, source="test")
        assert isinstance(report, GeneratedReport)
        assert report.file_size == len(content)
        if head:
            assert content[:len(head)] == head

    def test_daily_ops_html(self, fleet):
        _report, content, _data = generate(ReportType.DAILY_OPS, "html", {}, source="test")
        assert b"<html" in content.lower() or b"<!doctype" in content.lower()

    def test_unsupported_format_raises(self, fleet):
        with pytest.raises(ValueError):
            generate(ReportType.COMPLIANCE_SUMMARY, "html", {})  # html not offered for compliance


# ── endpoints ────────────────────────────────────────────────────────────────

class TestEndpoints:
    def test_compliance_json(self, fleet, auth_client):
        resp = auth_client.post("/api/reports/compliance-summary/",
                                {"format": "json"}, format="json")
        assert resp.status_code == 200
        assert "summary" in resp.json()

    def test_compliance_pdf_download(self, fleet, auth_client):
        resp = auth_client.post("/api/reports/compliance-summary/",
                                {"format": "pdf"}, format="json")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert b"".join(resp.streaming_content)[:5] == b"%PDF-"

    def test_daily_ops_csv(self, fleet, auth_client):
        resp = auth_client.post("/api/reports/daily-ops/", {"format": "csv"}, format="json")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")

    def test_history_and_download(self, fleet, auth_client):
        auth_client.post("/api/reports/daily-ops/", {"format": "pdf"}, format="json")
        lst = auth_client.get("/api/reports/")
        assert lst.status_code == 200
        rows = lst.json()["results"] if isinstance(lst.json(), dict) else lst.json()
        assert len(rows) >= 1
        rid = rows[0]["id"]
        dl = auth_client.get(f"/api/reports/{rid}/download/")
        assert dl.status_code == 200
        assert b"".join(dl.streaming_content)[:5] == b"%PDF-"

    def test_requires_auth(self, api_client):
        assert api_client.post("/api/reports/compliance-summary/", {}, format="json").status_code in (401, 403)


# ── scheduling ───────────────────────────────────────────────────────────────

class TestScheduling:
    def test_create_and_list_schedule(self, auth_client):
        resp = auth_client.post("/api/reports/compliance-summary/schedule/", {
            "frequency": "weekly", "hour": 8, "day_of_week": 0, "fmt": "pdf",
            "recipients": ["admin@example.com"],
        }, format="json")
        assert resp.status_code == 201
        lst = auth_client.get("/api/reports/compliance-summary/schedule/")
        assert len(lst.json()) == 1
        assert lst.json()[0]["report_type"] == "compliance_summary"

    def test_is_due_logic(self):
        from datetime import datetime
        from apps.reports.tasks import _is_due
        sched = ReportSchedule(report_type=ReportType.DAILY_OPS, frequency="daily", hour=8)
        due = timezone.make_aware(datetime(2026, 6, 14, 8, 5))
        notyet = timezone.make_aware(datetime(2026, 6, 14, 9, 5))
        assert _is_due(sched, due) is True
        assert _is_due(sched, notyet) is False
        sched.last_run = due
        assert _is_due(sched, due) is False     # already ran today

    def test_run_due_generates(self, fleet):
        from apps.reports.tasks import run_due_schedules
        from datetime import datetime
        ReportSchedule.objects.create(report_type=ReportType.DAILY_OPS, frequency="daily",
                                      hour=8, fmt="pdf", recipients=[])
        now = timezone.make_aware(datetime(2026, 6, 14, 8, 1))
        fired = run_due_schedules(now=now)
        assert fired == 1
        assert GeneratedReport.objects.filter(source="scheduled").count() == 1


# ── Reporting periods (weekly/monthly/quarterly) ─────────────────────────────

class TestPeriods:
    def test_period_bounds(self):
        import datetime as _dt
        from apps.reports.daily_ops import _period_bounds
        # 2026-06-13 is a Saturday.
        s, e, sd, ed, label = _period_bounds("daily", "2026-06-13")
        assert sd == _dt.date(2026, 6, 13) and ed == sd and label == "June 13, 2026"
        s, e, sd, ed, label = _period_bounds("weekly", "2026-06-13")
        assert sd == _dt.date(2026, 6, 7) and ed == _dt.date(2026, 6, 13)
        assert "Week" in label
        s, e, sd, ed, label = _period_bounds("monthly", "2026-06-13")
        assert sd == _dt.date(2026, 6, 1) and ed == _dt.date(2026, 6, 13) and label == "June 2026"
        s, e, sd, ed, label = _period_bounds("quarterly", "2026-06-13")
        assert sd == _dt.date(2026, 4, 1) and label.startswith("Q2 2026")

    def test_build_ops_report_weekly_has_period_meta_and_trends(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        from apps.reports.daily_ops import build_ops_report
        data = build_ops_report(period="weekly", end_date="2026-06-13")
        assert data["period_name"] == "weekly"
        assert data["report_title"] == "Weekly Operations Report"
        assert data["trends"] is not None and len(data["trends"]["days"]) == 7
        assert data["comparison"] is not None
        assert "security_failures_change_pct" in data["comparison"]

    def test_daily_has_no_trends(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        from apps.reports.daily_ops import build_ops_report
        data = build_ops_report(period="daily", end_date="2026-06-13")
        assert data["period_name"] == "daily"
        assert data["trends"] is None and data["comparison"] is None

    def test_ops_endpoint_weekly_pdf(self, fleet, auth_client, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        resp = auth_client.post("/api/reports/ops/",
                                {"period": "weekly", "format": "pdf", "end_date": "2026-06-13"},
                                format="json")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert b"".join(resp.streaming_content)[:5] == b"%PDF-"

    def test_ops_endpoint_monthly_json(self, fleet, auth_client, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        resp = auth_client.post("/api/reports/ops/",
                                {"period": "monthly", "format": "json", "end_date": "2026-06-13"},
                                format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["period_name"] == "monthly" and body["report_title"] == "Monthly Operations Report"

    def test_quarterly_schedule_is_due(self):
        import datetime as _dt
        from apps.reports.tasks import _is_due
        sched = ReportSchedule(report_type=ReportType.DAILY_OPS, frequency="quarterly",
                               hour=7, day_of_month=1)
        due = timezone.make_aware(_dt.datetime(2026, 7, 1, 7, 5))      # Jul 1
        off = timezone.make_aware(_dt.datetime(2026, 6, 1, 7, 5))      # Jun 1 (not a quarter start)
        assert _is_due(sched, due) is True
        assert _is_due(sched, off) is False

    def test_download_filename_period(self, fleet, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        from apps.reports.generate import generate
        from apps.reports.storage import download_filename
        rep, _content, _data = generate(ReportType.DAILY_OPS, "pdf",
                                        {"period": "weekly", "end_date": "2026-06-13"}, source="test")
        assert download_filename(rep).startswith("spane-weekly-ops-")


# ── Delete (single + bulk) ───────────────────────────────────────────────────

class TestDelete:
    def _make(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.logs.views._execute", lambda body: {"hits": {"hits": []}})
        auth_client.post("/api/reports/daily-ops/", {"format": "pdf"}, format="json")

    def test_delete_single(self, fleet, auth_client, monkeypatch):
        self._make(auth_client, monkeypatch)
        rep = GeneratedReport.objects.first()
        import os
        from django.conf import settings
        path = os.path.join(settings.MEDIA_ROOT, rep.file_path)
        assert os.path.exists(path)
        resp = auth_client.delete(f"/api/reports/{rep.id}/")
        assert resp.status_code == 204
        assert not GeneratedReport.objects.filter(pk=rep.id).exists()
        assert not os.path.exists(path)  # file cleaned up

    def test_bulk_delete(self, fleet, auth_client, monkeypatch):
        self._make(auth_client, monkeypatch)
        self._make(auth_client, monkeypatch)
        ids = list(GeneratedReport.objects.values_list("id", flat=True))
        assert len(ids) == 2
        resp = auth_client.post("/api/reports/bulk-delete/", {"ids": ids}, format="json")
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert GeneratedReport.objects.count() == 0

    def test_bulk_delete_requires_ids(self, fleet, auth_client):
        resp = auth_client.post("/api/reports/bulk-delete/", {"ids": []}, format="json")
        assert resp.status_code == 400


class TestExceptionScrubbing:
    def test_value_error_is_scrubbed(self, fleet, auth_client, monkeypatch):
        # A ValueError from generate() must not echo its raw text to the client.
        def _boom(*a, **k):
            raise ValueError("sensitive internal detail /srv/secret")
        monkeypatch.setattr("apps.reports.views.generate", _boom)
        resp = auth_client.post("/api/reports/daily-ops/", {"format": "pdf"}, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"] == "Invalid report request."
        assert "sensitive" not in str(resp.content)
