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
        for key in ("security_events", "device_availability", "compliance_events",
                    "config_changes", "collection_health", "agent_health", "alerts_summary"):
            assert key in data

    def test_login_failures_counted(self, fleet):
        # an event "yesterday" (the default report day)
        when = timezone.now() - __import__("datetime").timedelta(days=1)
        log = AuditLog.objects.create(event_type=AuditLog.EventType.LOGIN_FAILED,
                                      username="admin", ip_address="10.150.1.45")
        AuditLog.objects.filter(pk=log.pk).update(created_at=when)
        data = dops.build_daily_ops(date=when.date().isoformat())
        assert data["security_events"]["total_failures"] == 1
        assert data["security_events"]["unique_sources"] == 1

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
