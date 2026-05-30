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
                    "source_ip": "172.18.0.1", "raw": "<134>..."}},
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
        assert body["summary"]["by_severity"]["warning"] == 1
        assert body["summary"]["by_severity"]["info"] == 1
        assert body["summary"]["by_severity"]["critical"] == 0

        # query built correctly
        musts = captured["body"]["query"]["bool"]["must"]
        assert {"term": {"hostname.keyword": "router-a"}} in musts
        assert {"terms": {"severity_name.keyword": ["info", "informational", "warn", "warning"]}} in musts
        assert {"match": {"message": "BGP"}} in musts

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
