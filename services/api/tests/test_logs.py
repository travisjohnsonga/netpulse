import pytest

from apps.logs import views as logs_views

pytestmark = pytest.mark.django_db


def _fake_response():
    return {
        "hits": {
            "total": {"value": 2},
            "hits": [
                {"_id": "a", "_source": {
                    "timestamp": "2026-05-29T22:15:00", "hostname": "router-a",
                    "severity_name": "warning", "facility_name": "local0",
                    "message": "BGP: Neighbor down", "app_name": "BGP", "proc_id": None,
                    "source_ip": "172.18.0.1", "source": "router-a", "raw": "<134>..."}},
                {"_id": "b", "_source": {
                    "timestamp": "2026-05-29T22:14:00", "hostname": "router-a",
                    "severity_name": "info", "facility_name": "local0",
                    "message": "Interface up", "app_name": "OSPF"}},
            ],
        },
        "aggregations": {"by_severity": {"buckets": [
            {"key": "warning", "doc_count": 1}, {"key": "info", "doc_count": 1},
        ]}},
    }


class TestLogQuery:
    def test_basic_query(self, auth_client, monkeypatch):
        captured = {}
        def fake_exec(body):
            captured["body"] = body
            return _fake_response()
        monkeypatch.setattr(logs_views, "_execute", fake_exec)

        resp = auth_client.get("/api/logs/?device_hostname=router-a&severity=warning,info&search=BGP")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert len(body["results"]) == 2
        r0 = body["results"][0]
        assert r0["hostname"] == "router-a" and r0["severity"] == "warning"
        assert r0["severity_label"] == "Warning" and r0["facility"] == "local0"
        assert r0["program"] == "BGP" and r0["source_ip"] == "172.18.0.1"
        # `source` is surfaced so the UI can distinguish device syslog (source =
        # device identifier) from agent-forwarded logs (source = auth/service/…).
        assert r0["source"] == "router-a"
        assert body["summary"]["by_severity"]["warning"] == 1
        assert body["summary"]["by_severity"]["info"] == 1
        assert body["summary"]["by_severity"]["critical"] == 0

        # query built correctly
        musts = captured["body"]["query"]["bool"]["must"]
        assert {"term": {"hostname.keyword": "router-a"}} in musts
        assert {"terms": {"severity_name.keyword": ["info", "informational", "warn", "warning"]}} in musts
        assert {"match": {"message": "BGP"}} in musts

    def test_time_filter_uses_at_timestamp(self, auth_client, monkeypatch):
        # Regression: the syslog `timestamp` field is often null, so the range
        # filter + sort must target @timestamp or every time window returns empty.
        captured = {}
        monkeypatch.setattr(logs_views, "_execute", lambda body: captured.update(body) or _fake_response())
        auth_client.get("/api/logs/?from=2026-05-24T00:00:00Z&to=2026-05-31T00:00:00Z")
        musts = captured["query"]["bool"]["must"]
        assert {"range": {"@timestamp": {"gte": "2026-05-24T00:00:00Z", "lte": "2026-05-31T00:00:00Z"}}} in musts
        assert captured["sort"] == [{"@timestamp": {"order": "desc"}}]
        # The null-prone `timestamp` field must NOT be used for ranges.
        assert all("timestamp" not in (m.get("range") or {}) for m in musts)

    def test_pagination_params(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(logs_views, "_execute", lambda body: captured.update(body) or _fake_response())
        auth_client.get("/api/logs/?page=3&page_size=25")
        assert captured["from"] == 50 and captured["size"] == 25

    def test_store_unavailable_degrades(self, auth_client, monkeypatch):
        def boom(body):
            raise RuntimeError("connection refused")
        monkeypatch.setattr(logs_views, "_execute", boom)
        resp = auth_client.get("/api/logs/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0
        assert resp.json()["summary"]["by_severity"]["error"] == 0

    def test_site_filter_resolves_hostnames_and_ips(self, auth_client, monkeypatch):
        from apps.devices.models import Device, Site
        site = Site.objects.create(name="DC-X")
        Device.objects.create(hostname="sw-x1", ip_address="10.0.1.1", site=site)
        captured = {}
        monkeypatch.setattr(logs_views, "_execute", lambda body: captured.update(body) or _fake_response())
        auth_client.get(f"/api/logs/?site={site.id}")
        musts = captured["query"]["bool"]["must"]
        # Matches hostname OR source_ip against the device's identifiers.
        assert {"bool": {"should": [
            {"terms": {"hostname.keyword": ["10.0.1.1", "sw-x1"]}},
            {"terms": {"source_ip.keyword": ["10.0.1.1", "sw-x1"]}},
        ], "minimum_should_match": 1}} in musts

    def test_known_hostname_matches_hostname_or_ip(self, auth_client, monkeypatch):
        from apps.devices.models import Device
        Device.objects.create(hostname="router-a", ip_address="192.0.2.7")
        captured = {}
        monkeypatch.setattr(logs_views, "_execute", lambda body: captured.update({"body": body}) or _fake_response())
        auth_client.get("/api/logs/?device_hostname=router-a")
        musts = captured["body"]["query"]["bool"]["must"]
        assert {"bool": {"should": [
            {"terms": {"hostname.keyword": ["192.0.2.7", "router-a"]}},
            {"terms": {"source_ip.keyword": ["192.0.2.7", "router-a"]}},
        ], "minimum_should_match": 1}} in musts

    def test_unknown_hostname_falls_back_to_exact(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(logs_views, "_execute", lambda body: captured.update({"body": body}) or _fake_response())
        auth_client.get("/api/logs/?device_hostname=ghost")
        musts = captured["body"]["query"]["bool"]["must"]
        assert {"term": {"hostname.keyword": "ghost"}} in musts

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/logs/").status_code == 401


class TestLogFilters:
    def test_crud(self, auth_client):
        from apps.logs.models import LogFilter
        resp = auth_client.post("/api/logs/filters/", {
            "name": "Noise", "pattern": r"hpe-restd.*AMM", "action": "suppress",
            "platforms": ["aos_cx"],
        }, format="json")
        assert resp.status_code == 201, resp.content
        fid = resp.json()["id"]
        assert resp.json()["platforms"] == ["aos_cx"]

        assert auth_client.get("/api/logs/filters/").json()["count"] == 1

        resp = auth_client.patch(f"/api/logs/filters/{fid}/", {"enabled": False}, format="json")
        assert resp.status_code == 200
        assert LogFilter.objects.get(id=fid).enabled is False

        assert auth_client.delete(f"/api/logs/filters/{fid}/").status_code == 204

    def test_invalid_regex_rejected_on_create(self, auth_client):
        resp = auth_client.post("/api/logs/filters/", {
            "name": "Bad", "pattern": r"[unclosed", "action": "suppress",
        }, format="json")
        assert resp.status_code == 400
        assert "pattern" in resp.json()

    def test_test_endpoint_match(self, auth_client):
        resp = auth_client.post("/api/logs/filters/test/", {
            "pattern": r"hpe-restd.*AMM", "message": "hpe-restd: [AMM] User logged in",
        }, format="json")
        assert resp.status_code == 200
        assert resp.json() == {"matches": True, "error": None}

    def test_test_endpoint_no_match(self, auth_client):
        resp = auth_client.post("/api/logs/filters/test/", {
            "pattern": r"hpe-restd.*AMM", "message": "ospf neighbor up",
        }, format="json")
        assert resp.json() == {"matches": False, "error": None}

    def test_test_endpoint_invalid_regex(self, auth_client):
        resp = auth_client.post("/api/logs/filters/test/", {
            "pattern": r"[bad", "message": "x",
        }, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["matches"] is False and body["error"]

    def test_suppress_filter_removes_matching_logs(self, auth_client, monkeypatch):
        from apps.logs.models import LogFilter
        LogFilter.objects.create(name="drop-bgp", pattern=r"BGP", action="suppress")
        monkeypatch.setattr(logs_views, "_execute", lambda body: _fake_response())
        resp = auth_client.get("/api/logs/")
        assert resp.status_code == 200
        msgs = [r["message"] for r in resp.json()["results"]]
        assert "BGP: Neighbor down" not in msgs
        assert "Interface up" in msgs
        assert resp["X-Suppressed-Count"] == "1"

    def test_apply_filters_false_bypasses(self, auth_client, monkeypatch):
        from apps.logs.models import LogFilter
        LogFilter.objects.create(name="drop-bgp", pattern=r"BGP", action="suppress")
        monkeypatch.setattr(logs_views, "_execute", lambda body: _fake_response())
        resp = auth_client.get("/api/logs/?apply_filters=false")
        assert len(resp.json()["results"]) == 2
        assert resp["X-Suppressed-Count"] == "0"

    def test_disabled_filter_not_applied(self, auth_client, monkeypatch):
        from apps.logs.models import LogFilter
        LogFilter.objects.create(name="drop-bgp", pattern=r"BGP", action="suppress", enabled=False)
        monkeypatch.setattr(logs_views, "_execute", lambda body: _fake_response())
        resp = auth_client.get("/api/logs/")
        assert len(resp.json()["results"]) == 2

    def test_platform_scoped_suppress(self, auth_client, monkeypatch):
        # router-a is ios_xe; a filter scoped to aos_cx must NOT suppress it,
        # while one scoped to ios_xe (or unscoped) must.
        from apps.devices.models import Device
        from apps.logs.models import LogFilter
        Device.objects.create(hostname="router-a", ip_address="192.0.2.50", platform="ios_xe")
        LogFilter.objects.create(name="aoscx-only", pattern=r"BGP", action="suppress",
                                 platforms=["aos_cx"])
        monkeypatch.setattr(logs_views, "_execute", lambda body: _fake_response())
        resp = auth_client.get("/api/logs/")
        assert resp["X-Suppressed-Count"] == "0"  # platform mismatch → not suppressed

        LogFilter.objects.create(name="iosxe-only", pattern=r"BGP", action="suppress",
                                 platforms=["ios_xe"])
        resp = auth_client.get("/api/logs/")
        assert resp["X-Suppressed-Count"] == "1"

    def test_seed_log_filters_idempotent(self):
        from django.core.management import call_command
        from apps.logs.models import LogFilter
        call_command("seed_log_filters")
        first = LogFilter.objects.count()
        assert first == 2
        assert LogFilter.objects.filter(enabled=True).count() == 0
        call_command("seed_log_filters")
        assert LogFilter.objects.count() == first
