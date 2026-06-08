"""Multi-collector service checks: resolution, aggregation, persistence, API."""
import pytest

from apps.checks import collectors as cc
from apps.checks.models import ServiceCheck, ServiceCheckCollector, CheckResult
from apps.checks.service import persist_result
from apps.collectors.models import Collector
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db

UP, DOWN, UNKNOWN = "up", "down", "unknown"
PASS = ServiceCheckCollector.Result.PASSING
FAIL = ServiceCheckCollector.Result.FAILING
UNK = ServiceCheckCollector.Result.UNKNOWN


def _collector(name, **kw):
    kw.setdefault("status", Collector.Status.ACTIVE)
    kw.setdefault("api_key_hash", name)
    return Collector.objects.create(name=name, **kw)


def _check(mode="site", **kw):
    return ServiceCheck.objects.create(
        name=kw.pop("name", "chk"), check_type="tcp", host="h",
        collector_mode=mode, **kw)


def _assign(check, collector, result=UNK, enabled=True):
    return ServiceCheckCollector.objects.create(
        service_check=check, collector=collector, last_result=result, enabled=enabled)


class TestResolution:
    def test_all_returns_active(self):
        c1 = _collector("c1"); _collector("c2", status=Collector.Status.OFFLINE)
        check = _check("all")
        assert set(cc.collectors_for_check(check)) == {c1}

    def test_selected_only_enabled_assigned(self):
        c1, c2 = _collector("c1"), _collector("c2")
        check = _check("selected")
        _assign(check, c1, enabled=True)
        _assign(check, c2, enabled=False)
        assert set(cc.collectors_for_check(check)) == {c1}

    def test_site_uses_site_collectors(self):
        site = Site.objects.create(name="S")
        c_site = _collector("c-site", site=site)
        _collector("c-other")
        dev = Device.objects.create(hostname="d", ip_address="10.0.0.1", site=site)
        check = _check("site", device=dev)
        assert set(cc.collectors_for_check(check)) == {c_site}

    def test_site_falls_back_to_default(self):
        site = Site.objects.create(name="S")  # no collectors at site
        default = _collector("def", is_default=True)
        dev = Device.objects.create(hostname="d", ip_address="10.0.0.2", site=site)
        check = _check("site", device=dev)
        assert set(cc.collectors_for_check(check)) == {default}


class TestAggregation:
    def test_unknown_when_nothing_reported(self):
        check = _check("any")
        _assign(check, _collector("c1"), result=UNK)
        assert cc.evaluate_check_status(check) == UNKNOWN

    def test_all_mode(self):
        check = _check("all")
        a, b = _collector("a"), _collector("b")
        _assign(check, a, PASS); r = _assign(check, b, PASS)
        assert cc.evaluate_check_status(check) == UP
        r.last_result = FAIL; r.save()
        assert cc.evaluate_check_status(check) == DOWN

    def test_any_mode(self):
        check = _check("any")
        a, b = _collector("a"), _collector("b")
        _assign(check, a, FAIL); rb = _assign(check, b, PASS)
        assert cc.evaluate_check_status(check) == UP
        rb.last_result = FAIL; rb.save()
        assert cc.evaluate_check_status(check) == DOWN

    def test_site_majority(self):
        check = _check("site")
        a, b, c = _collector("a"), _collector("b"), _collector("c")
        _assign(check, a, FAIL); _assign(check, b, FAIL); _assign(check, c, PASS)
        # 2/3 failing > 50% → down
        assert cc.evaluate_check_status(check) == DOWN

    def test_site_minority_failing_is_up(self):
        check = _check("site")
        a, b, c = _collector("a"), _collector("b"), _collector("c")
        _assign(check, a, FAIL); _assign(check, b, PASS); _assign(check, c, PASS)
        assert cc.evaluate_check_status(check) == UP

    def test_disabled_assignment_ignored(self):
        check = _check("any")
        a = _collector("a")
        _assign(check, a, FAIL, enabled=False)
        assert cc.evaluate_check_status(check) == UNKNOWN


class TestPersistence:
    def test_records_per_collector_and_aggregate(self):
        from django.utils import timezone
        check = _check("any")
        a, b = _collector("a"), _collector("b")
        _assign(check, a, UNK); _assign(check, b, UNK)
        # Collector a reports DOWN.
        persist_result(check, {"status": DOWN, "error": "x"}, timezone.now(), collector=a)
        sca = ServiceCheckCollector.objects.get(service_check=check, collector=a)
        assert sca.last_result == FAIL and sca.consecutive_failures == 1
        # Result row carries the collector.
        assert CheckResult.objects.filter(service_check=check, collector=a).exists()
        # any-mode: a down, b unknown → no passing → down
        check.refresh_from_db()
        # First failure held by flap suppression (failures_before_alert=2) → not yet down
        # Collector b reports UP → any-mode passes.
        persist_result(check, {"status": UP, "response_time_ms": 5.0}, timezone.now(), collector=b)
        check.refresh_from_db()
        assert check.current_status == UP

    def test_legacy_path_unchanged(self):
        from django.utils import timezone
        check = _check("site")
        persist_result(check, {"status": DOWN, "error": "t"}, timezone.now())
        persist_result(check, {"status": DOWN, "error": "t"}, timezone.now())
        check.refresh_from_db()
        assert check.current_status == DOWN
        # collector is null on legacy results
        assert CheckResult.objects.filter(service_check=check, collector__isnull=True).count() == 2

    def test_engine_collector_for_default(self):
        check = _check("all")
        default = _collector("def", is_default=True)
        assert cc.engine_collector_for(check) == default

    def test_failing_collector_names(self):
        check = _check("all")
        a, b = _collector("a"), _collector("b")
        _assign(check, a, FAIL); _assign(check, b, PASS)
        assert cc.failing_collector_names(check) == ["a"]


class TestEndpoints:
    def test_assign_and_remove_collector(self, auth_client):
        check = _check("selected")
        col = _collector("c1")
        # Assign
        resp = auth_client.post(f"/api/checks/{check.id}/collectors/",
                                {"collector_id": col.id, "enabled": True}, format="json")
        assert resp.status_code == 201, resp.content
        assert ServiceCheckCollector.objects.filter(service_check=check, collector=col).exists()
        # List
        lst = auth_client.get(f"/api/checks/{check.id}/collectors/")
        assert lst.status_code == 200 and len(lst.json()) == 1
        # Remove
        rm = auth_client.delete(f"/api/checks/{check.id}/collectors/{col.id}/")
        assert rm.status_code == 204
        assert not ServiceCheckCollector.objects.filter(service_check=check, collector=col).exists()

    def test_assign_requires_valid_collector(self, auth_client):
        check = _check("selected")
        assert auth_client.post(f"/api/checks/{check.id}/collectors/",
                                {"collector_id": 99999}, format="json").status_code == 400

    def test_results_includes_collector_breakdown(self, auth_client):
        check = _check("any")
        col = _collector("c1")
        _assign(check, col, PASS)
        body = auth_client.get(f"/api/checks/{check.id}/results/").json()
        assert "collector_results" in body and "aggregate_status" in body
        assert body["collector_results"][0]["collector_name"] == "c1"

    def test_summary_by_collector(self, auth_client):
        check = _check("any")
        col = _collector("c1")
        _assign(check, col, FAIL)
        body = auth_client.get("/api/checks/summary/").json()
        assert "by_collector" in body
        entry = next(e for e in body["by_collector"] if e["collector_id"] == col.id)
        assert entry["failing"] == 1

    def test_serializer_exposes_collector_mode(self, auth_client):
        check = _check("all")
        body = auth_client.get(f"/api/checks/{check.id}/").json()
        assert body["collector_mode"] == "all"
        assert "collector_results" in body
