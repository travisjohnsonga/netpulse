"""DNS enrichment for Flow Analytics — apps.flows.dns + the resolve endpoints."""
import pytest
from django.core.cache import cache

from apps.flows import dns

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _fake_rdns(mapping):
    """Return a gethostbyaddr stub: known IPs resolve, others raise herror."""
    import socket

    def _g(ip):
        if ip in mapping:
            return (mapping[ip], [], [ip])
        raise socket.herror("not found")
    return _g


class TestResolveIps:
    def test_reverse_dns_and_fallback(self, monkeypatch):
        monkeypatch.setattr(dns.socket, "gethostbyaddr",
                            _fake_rdns({"8.8.8.8": "dns.google"}))
        out = dns.resolve_ips(["8.8.8.8", "203.0.113.9"])
        assert out["resolved"]["8.8.8.8"] == "dns.google"
        assert out["resolved"]["203.0.113.9"] == "203.0.113.9"   # unresolved → IP
        assert out["resolved_now"] == 1 and out["failed"] == 1

    def test_inventory_wins_over_dns(self, monkeypatch):
        from apps.devices.models import Device
        Device.objects.create(hostname="sw-mdf-01", ip_address="10.150.0.15",
                              management_ip="10.150.0.15")
        # DNS would say something else, but inventory must win (and no DNS call).
        called = {"n": 0}

        def _boom(ip):
            called["n"] += 1
            raise OSError("should not be called for inventory IP")
        monkeypatch.setattr(dns.socket, "gethostbyaddr", _boom)
        out = dns.resolve_ips(["10.150.0.15"])
        assert out["resolved"]["10.150.0.15"] == "sw-mdf-01"
        assert out["from_inventory"] == 1
        assert called["n"] == 0

    def test_cache_hit_on_second_call(self, monkeypatch):
        calls = {"n": 0}

        def _g(ip):
            calls["n"] += 1
            return ("host.example.com", [], [ip])
        monkeypatch.setattr(dns.socket, "gethostbyaddr", _g)
        dns.resolve_ips(["198.51.100.7"])
        first = calls["n"]
        out2 = dns.resolve_ips(["198.51.100.7"])
        assert calls["n"] == first                # served from cache, no new lookup
        assert out2["cached"] == 1
        assert out2["resolved"]["198.51.100.7"] == "host.example.com"

    def test_invalid_ips_dropped_and_capped(self, monkeypatch):
        monkeypatch.setattr(dns.socket, "gethostbyaddr", _fake_rdns({}))
        out = dns.resolve_ips(["not-an-ip", "10.0.0.1", "10.0.0.1"])  # dupe + junk
        assert "not-an-ip" not in out["resolved"]
        assert out["total"] == 1                  # deduped + validated


class TestResolveEndpoint:
    def test_post_resolve(self, auth_client, monkeypatch):
        monkeypatch.setattr(dns.socket, "gethostbyaddr",
                            _fake_rdns({"1.1.1.1": "one.one.one.one"}))
        resp = auth_client.post("/api/flows/resolve/", {"ips": ["1.1.1.1", "203.0.113.1"]}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolved"]["1.1.1.1"] == "one.one.one.one"
        assert body["resolved"]["203.0.113.1"] == "203.0.113.1"

    def test_empty_ips(self, auth_client):
        resp = auth_client.post("/api/flows/resolve/", {"ips": []}, format="json")
        assert resp.status_code == 200
        assert resp.json()["resolved"] == {}

    def test_bad_payload(self, auth_client):
        resp = auth_client.post("/api/flows/resolve/", {"ips": "nope"}, format="json")
        assert resp.status_code == 400

    def test_clear_cache_admin_only(self, auth_client, viewer_client):
        # viewer (non-admin) is forbidden
        assert viewer_client.post("/api/flows/resolve/clear-cache/").status_code == 403
        # admin (default auth_client user) succeeds
        resp = auth_client.post("/api/flows/resolve/clear-cache/")
        assert resp.status_code == 200
        assert "cleared" in resp.json()
