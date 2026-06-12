import pytest

from apps.flows import views as flow_views

pytestmark = pytest.mark.django_db


def _flow_hits():
    return {
        "hits": {
            "total": {"value": 2},
            "hits": [
                {"_id": "f1", "_source": {
                    "@timestamp": "2026-06-06T01:01:38Z", "exporter_ip": "192.168.98.254",
                    "protocol_version": "netflow5", "src_ip": "192.168.98.254",
                    "dst_ip": "192.168.98.158", "src_port": 0, "dst_port": 0,
                    "ip_protocol": 1, "bytes": 48, "packets": 1, "duration_ms": 0.0,
                    "input_if": 1, "output_if": 2, "tcp_flags": 0, "tos": 0}},
                {"_id": "f2", "_source": {
                    "@timestamp": "2026-06-06T01:01:30Z", "exporter_ip": "192.168.98.254",
                    "protocol_version": "netflow5", "src_ip": "192.168.98.100",
                    "dst_ip": "1.1.1.1", "src_port": 51514, "dst_port": 443,
                    "ip_protocol": 6, "bytes": 1500, "packets": 4}},
            ],
        },
    }


class TestFlowQuery:
    def test_basic_query_shape(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        resp = auth_client.get("/api/flows/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        r0 = body["results"][0]
        assert r0["protocol"] == "ICMP" and r0["ip_protocol"] == 1
        assert r0["src_ip"] == "192.168.98.254" and r0["bytes"] == 48
        r1 = body["results"][1]
        assert r1["protocol"] == "TCP" and r1["service"] == "HTTPS"
        # default window 1h, sorted desc, capped size
        assert {"range": {"@timestamp": {"gte": "now-1h"}}} in captured["query"]["bool"]["must"]
        assert captured["sort"] == [{"@timestamp": {"order": "desc"}}]
        assert captured["size"] == 100

    def test_filters_build_query(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        auth_client.get("/api/flows/?src_ip=10.0.0.1&dst_ip=10.0.0.2&protocol=tcp&window=6h&limit=5")
        musts = captured["query"]["bool"]["must"]
        assert {"term": {"src_ip.keyword": "10.0.0.1"}} in musts
        assert {"term": {"dst_ip.keyword": "10.0.0.2"}} in musts
        assert {"term": {"ip_protocol": 6}} in musts
        assert {"range": {"@timestamp": {"gte": "now-6h"}}} in musts
        assert captured["size"] == 5

    def test_limit_capped_at_1000(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        auth_client.get("/api/flows/?limit=99999")
        assert captured["size"] == 1000

    def test_device_id_resolves_exporter(self, auth_client, monkeypatch):
        from apps.devices.models import Device
        Device.objects.create(hostname="exp1", ip_address="192.168.98.254")
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        dev = Device.objects.get(hostname="exp1")
        auth_client.get(f"/api/flows/?device_id={dev.id}")
        assert {"term": {"exporter_ip.keyword": "192.168.98.254"}} in captured["query"]["bool"]["must"]

    def test_unknown_device_matches_nothing(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        auth_client.get("/api/flows/?device_id=99999")
        assert {"term": {"exporter_ip.keyword": "__none__"}} in captured["query"]["bool"]["must"]

    def test_site_filter_restricts_to_site_exporters(self, auth_client, monkeypatch):
        from apps.devices.models import Device, Site
        site = Site.objects.create(name="DC-1")
        Device.objects.create(hostname="d1", ip_address="10.0.0.1", site=site)
        # management_ip takes precedence over ip_address when present.
        Device.objects.create(hostname="d2", ip_address="10.0.0.2", management_ip="172.16.0.2", site=site)
        Device.objects.create(hostname="other", ip_address="10.9.9.9")  # different site → excluded
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        auth_client.get(f"/api/flows/?site={site.id}")
        terms = [m for m in captured["query"]["bool"]["must"] if "terms" in m]
        assert len(terms) == 1
        ips = set(terms[0]["terms"]["exporter_ip.keyword"])
        assert ips == {"10.0.0.1", "172.16.0.2"}

    def test_empty_site_matches_nothing(self, auth_client, monkeypatch):
        from apps.devices.models import Site
        site = Site.objects.create(name="Empty")  # no devices
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        auth_client.get(f"/api/flows/?site={site.id}")
        assert {"term": {"exporter_ip.keyword": "__none__"}} in captured["query"]["bool"]["must"]

    def test_store_unavailable_degrades(self, auth_client, monkeypatch):
        def boom(body):
            raise RuntimeError("connection refused")
        monkeypatch.setattr(flow_views, "_execute", boom)
        resp = auth_client.get("/api/flows/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "results": []}

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/flows/").status_code == 401


class TestTopTalkers:
    def _agg(self):
        return {"aggregations": {"top_src": {"buckets": [
            {"key": "192.168.98.100", "doc_count": 1234,
             "total_bytes": {"value": 1258291.0}, "total_packets": {"value": 4567.0}},
            {"key": "192.168.98.254", "doc_count": 567,
             "total_bytes": {"value": 466944.0}, "total_packets": {"value": 1200.0}},
        ]}}}

    def test_top_talkers_by_bytes(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        resp = auth_client.get("/api/flows/top-talkers/?by=bytes&limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["by"] == "bytes"
        assert body["results"][0] == {
            "src_ip": "192.168.98.100", "flows": 1234, "bytes": 1258291, "packets": 4567,
        }
        agg = captured["aggs"]["top_src"]["terms"]
        assert agg["field"] == "src_ip.keyword" and agg["size"] == 10
        assert agg["order"] == {"total_bytes": "desc"}

    def test_top_talkers_by_flows_orders_by_count(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        auth_client.get("/api/flows/top-talkers/?by=flows")
        assert captured["aggs"]["top_src"]["terms"]["order"] == {"_count": "desc"}

    def test_top_talkers_degrades(self, auth_client, monkeypatch):
        monkeypatch.setattr(flow_views, "_execute", lambda body: (_ for _ in ()).throw(RuntimeError()))
        resp = auth_client.get("/api/flows/top-talkers/")
        assert resp.status_code == 200
        assert resp.json()["results"] == []


class TestFlowSummary:
    def _agg(self):
        return {
            "hits": {"total": {"value": 35278}},
            "aggregations": {
                "total_bytes": {"value": 1234567.0},
                "total_packets": {"value": 45678.0},
                "unique_src": {"value": 12},
                "unique_dst": {"value": 45},
                "protocols": {"buckets": [
                    {"key": 1, "doc_count": 1200, "bytes": {"value": 57600.0}},
                    {"key": 6, "doc_count": 800, "bytes": {"value": 900000.0}},
                ]},
                "over_time": {"buckets": [
                    {"key_as_string": "2026-06-06T01:00:00Z", "key": 1, "bytes": {"value": 1234.0}},
                ]},
            },
        }

    def test_summary_shape(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        resp = auth_client.get("/api/flows/summary/?window=24h")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_flows"] == 35278
        assert body["total_bytes"] == 1234567
        assert body["unique_src_ips"] == 12 and body["unique_dst_ips"] == 45
        assert body["top_protocols"][0] == {"protocol": "ICMP", "flows": 1200, "bytes": 57600}
        assert body["bytes_over_time"][0] == {"timestamp": "2026-06-06T01:00:00Z", "bytes": 1234}
        # 24h window → 1h histogram buckets
        assert captured["aggs"]["over_time"]["date_histogram"]["fixed_interval"] == "1h"

    def test_summary_device_filter(self, auth_client, monkeypatch):
        from apps.devices.models import Device
        Device.objects.create(hostname="exp1", ip_address="10.5.5.5")
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        dev = Device.objects.get(hostname="exp1")
        auth_client.get(f"/api/flows/summary/?device_id={dev.id}")
        assert {"term": {"exporter_ip.keyword": "10.5.5.5"}} in captured["query"]["bool"]["must"]

    def test_summary_degrades(self, auth_client, monkeypatch):
        monkeypatch.setattr(flow_views, "_execute", lambda body: (_ for _ in ()).throw(RuntimeError()))
        resp = auth_client.get("/api/flows/summary/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_flows"] == 0 and body["top_protocols"] == []


class TestFlowDeviceSummary:
    def _agg(self):
        return {
            "hits": {"total": {"value": 100}},
            "aggregations": {
                "over_time": {"buckets": [
                    {"key_as_string": "2026-06-06T01:00:00Z", "key": 1,
                     "inbound": {"doc_count": 3, "bytes": {"value": 12345.0}},
                     "outbound": {"doc_count": 2, "bytes": {"value": 6789.0}}},
                ]},
                "protocols": {"buckets": [
                    {"key": 6, "doc_count": 234, "bytes": {"value": 123456.0}},
                    {"key": 17, "doc_count": 156, "bytes": {"value": 78901.0}},
                    {"key": 1, "doc_count": 89, "bytes": {"value": 45678.0}},
                    {"key": 47, "doc_count": 45, "bytes": {"value": 23456.0}},  # GRE → Other
                ]},
                "conversations": {"buckets": [
                    {"key": ["192.168.98.100", "8.8.8.8"], "doc_count": 45,
                     "total_bytes": {"value": 1234567.0}, "total_packets": {"value": 1234.0}},
                ]},
            },
        }

    def _device(self, ip="192.168.98.100"):
        from apps.devices.models import Device
        return Device.objects.create(hostname="dev1", ip_address=ip)

    def test_device_summary_shape(self, auth_client, monkeypatch):
        dev = self._device()
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        resp = auth_client.get(f"/api/flows/device-summary/?device_id={dev.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["traffic_over_time"][0] == {
            "timestamp": "2026-06-06T01:00:00Z", "inbound_bytes": 12345, "outbound_bytes": 6789,
        }
        # protocol mix collapses GRE into "Other" and computes byte-share pct
        mix = {m["protocol"]: m for m in body["protocol_mix"]}
        assert mix["TCP"]["bytes"] == 123456 and mix["TCP"]["flows"] == 234
        assert mix["Other"]["bytes"] == 23456 and mix["Other"]["flows"] == 45
        assert mix["TCP"]["pct"] == 45.5  # 123456 / 271491
        assert [m["protocol"] for m in body["protocol_mix"]] == ["TCP", "UDP", "ICMP", "Other"]
        assert body["top_conversations"][0] == {
            "src_ip": "192.168.98.100", "dst_ip": "8.8.8.8",
            "bytes": 1234567, "packets": 1234, "flows": 45,
        }

    def test_device_summary_query_is_src_or_dst(self, auth_client, monkeypatch):
        dev = self._device("10.5.5.5")
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        auth_client.get(f"/api/flows/device-summary/?device_id={dev.id}")
        q = captured["query"]["bool"]
        assert q["minimum_should_match"] == 1
        assert {"term": {"dst_ip.keyword": "10.5.5.5"}} in q["should"]
        assert {"term": {"src_ip.keyword": "10.5.5.5"}} in q["should"]
        # default 1h → 5m buckets
        assert captured["aggs"]["over_time"]["date_histogram"]["fixed_interval"] == "5m"

    def test_device_summary_interval_per_window(self, auth_client, monkeypatch):
        dev = self._device()
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        auth_client.get(f"/api/flows/device-summary/?device_id={dev.id}&window=24h")
        assert captured["aggs"]["over_time"]["date_histogram"]["fixed_interval"] == "2h"
        auth_client.get(f"/api/flows/device-summary/?device_id={dev.id}&window=7d")
        assert captured["aggs"]["over_time"]["date_histogram"]["fixed_interval"] == "12h"

    def test_device_summary_no_device_is_empty(self, auth_client, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(flow_views, "_execute", lambda body: called.__setitem__("n", called["n"] + 1) or self._agg())
        resp = auth_client.get("/api/flows/device-summary/")
        assert resp.status_code == 200
        assert resp.json() == {"window": "1h", "traffic_over_time": [], "protocol_mix": [], "top_conversations": []}
        assert called["n"] == 0  # no OpenSearch call without a resolvable device

    def test_device_summary_unknown_device_is_empty(self, auth_client, monkeypatch):
        monkeypatch.setattr(flow_views, "_execute", lambda body: self._agg())
        resp = auth_client.get("/api/flows/device-summary/?device_id=99999")
        assert resp.json()["top_conversations"] == []

    def test_device_summary_degrades(self, auth_client, monkeypatch):
        dev = self._device()
        monkeypatch.setattr(flow_views, "_execute", lambda body: (_ for _ in ()).throw(RuntimeError()))
        resp = auth_client.get(f"/api/flows/device-summary/?device_id={dev.id}")
        assert resp.status_code == 200
        assert resp.json()["protocol_mix"] == []


class TestFlowSankey:
    def _agg(self):
        return {"aggregations": {"conversations": {"buckets": [
            {"key": ["192.168.98.100", "8.8.8.8"], "doc_count": 45,
             "total_bytes": {"value": 1234567.0}, "total_packets": {"value": 1234.0}},
            {"key": ["192.168.98.254", "1.1.1.1"], "doc_count": 12,
             "total_bytes": {"value": 456789.0}, "total_packets": {"value": 678.0}},
            {"key": ["10.0.0.1", "10.0.0.1"], "doc_count": 3,
             "total_bytes": {"value": 100.0}, "total_packets": {"value": 1.0}},  # self-loop dropped
        ]}}}

    def test_sankey_shape(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        resp = auth_client.get("/api/flows/sankey/?window=6h&limit=20")
        assert resp.status_code == 200
        body = resp.json()
        # self-loop (10.0.0.1→10.0.0.1) dropped; two real conversations remain
        assert body["links"] == [
            {"source": "192.168.98.100", "target": "8.8.8.8", "value": 1234567,
             "bytes": 1234567, "packets": 1234, "flows": 45},
            {"source": "192.168.98.254", "target": "1.1.1.1", "value": 456789,
             "bytes": 456789, "packets": 678, "flows": 12},
        ]
        assert body["nodes"] == [
            {"name": "192.168.98.100"}, {"name": "8.8.8.8"},
            {"name": "192.168.98.254"}, {"name": "1.1.1.1"},
        ]
        agg = captured["aggs"]["conversations"]["multi_terms"]
        assert agg["size"] == 20 and agg["order"] == {"total_bytes": "desc"}
        assert {"range": {"@timestamp": {"gte": "now-6h"}}} in captured["query"]["bool"]["must"]

    def test_sankey_device_filter_src_or_dst(self, auth_client, monkeypatch):
        from apps.devices.models import Device
        dev = Device.objects.create(hostname="dev1", ip_address="192.168.98.100")
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or self._agg())
        auth_client.get(f"/api/flows/sankey/?device_id={dev.id}")
        musts = captured["query"]["bool"]["must"]
        assert {"bool": {"should": [
            {"term": {"src_ip.keyword": "192.168.98.100"}},
            {"term": {"dst_ip.keyword": "192.168.98.100"}},
        ], "minimum_should_match": 1}} in musts

    def test_sankey_unknown_device_empty(self, auth_client, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(flow_views, "_execute", lambda body: called.__setitem__("n", called["n"] + 1) or self._agg())
        resp = auth_client.get("/api/flows/sankey/?device_id=99999")
        assert resp.json() == {"window": "1h", "nodes": [], "links": []}
        assert called["n"] == 0

    def test_sankey_degrades(self, auth_client, monkeypatch):
        monkeypatch.setattr(flow_views, "_execute", lambda body: (_ for _ in ()).throw(RuntimeError()))
        resp = auth_client.get("/api/flows/sankey/")
        assert resp.status_code == 200
        assert resp.json()["links"] == []


class TestFlowSearch:
    def test_search_by_ip_src_or_dst(self, auth_client, monkeypatch):
        captured = {}
        monkeypatch.setattr(flow_views, "_execute", lambda body: captured.update(body) or _flow_hits())
        resp = auth_client.get("/api/flows/search/?ip=192.168.98.100&window=24h")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ip"] == "192.168.98.100"
        assert body["count"] == 2
        musts = captured["query"]["bool"]["must"]
        assert {"bool": {"should": [
            {"term": {"src_ip.keyword": "192.168.98.100"}},
            {"term": {"dst_ip.keyword": "192.168.98.100"}},
        ], "minimum_should_match": 1}} in musts
        assert {"range": {"@timestamp": {"gte": "now-24h"}}} in musts

    def test_search_without_ip_returns_empty(self, auth_client, monkeypatch):
        called = {"n": 0}
        def fake(body):
            called["n"] += 1
            return _flow_hits()
        monkeypatch.setattr(flow_views, "_execute", fake)
        resp = auth_client.get("/api/flows/search/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "results": [], "ip": ""}
        assert called["n"] == 0  # no OpenSearch call when no IP given


class TestProtocolHelpers:
    def test_protocol_and_service_names(self):
        from apps.flows.protocols import protocol_name, service_name
        assert protocol_name(6) == "TCP"
        assert protocol_name(17) == "UDP"
        assert protocol_name(1) == "ICMP"
        assert protocol_name(255) == "Proto 255"
        assert protocol_name("x") == "x"
        assert service_name(443) == "HTTPS"
        assert service_name(22) == "SSH"
        assert service_name(65000) is None
