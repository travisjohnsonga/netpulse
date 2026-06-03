import pytest
from apps.collectors.models import Collector

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def collector():
    return Collector.objects.create(
        name="Poller-DC1",
        api_key_hash="$2b$12$fakehashvalue12345678901234567890123456789012",
        status="active",
        version="1.2.0",
        remote_ip="10.10.0.50",
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestCollectorEndpoints:
    def test_list_collectors(self, auth_client, collector):
        resp = auth_client.get("/api/collectors/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_collector(self, auth_client):
        resp = auth_client.post("/api/collectors/", {
            "name": "Poller-Branch",
            "api_key_hash": "$2b$12$anotherfakehashvalue1234567890123456789012",
            "status": "pending",
        })
        assert resp.status_code == 201
        assert resp.json()["name"] == "Poller-Branch"
        assert resp.json()["status"] == "pending"

    def test_create_default_status_pending(self, auth_client):
        resp = auth_client.post("/api/collectors/", {
            "name": "New Poller",
            "api_key_hash": "uniquehash_new_poller_12345678901234567890123",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    def test_retrieve_collector(self, auth_client, collector):
        resp = auth_client.get(f"/api/collectors/{collector.pk}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Poller-DC1"

    def test_api_key_hash_in_response(self, auth_client, collector):
        resp = auth_client.get(f"/api/collectors/{collector.pk}/")
        # The hash is present (read-only), but it's the bcrypt hash — never plaintext
        assert "api_key_hash" in resp.json()

    def test_update_collector_status(self, auth_client, collector):
        resp = auth_client.patch(f"/api/collectors/{collector.pk}/", {"status": "offline"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "offline"

    def test_update_collector_version(self, auth_client, collector):
        resp = auth_client.patch(f"/api/collectors/{collector.pk}/", {"version": "1.3.0"})
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.3.0"

    def test_delete_collector(self, auth_client, collector):
        resp = auth_client.delete(f"/api/collectors/{collector.pk}/")
        assert resp.status_code == 204
        assert not Collector.objects.filter(pk=collector.pk).exists()

    def test_filter_by_status(self, auth_client, collector):
        Collector.objects.create(
            name="Revoked", status="revoked",
            api_key_hash="revokedhash_unique_1234567890123456789012345",
        )
        resp = auth_client.get("/api/collectors/?status=active")
        assert resp.status_code == 200
        assert all(c["status"] == "active" for c in resp.json()["results"])

    def test_search_by_name(self, auth_client, collector):
        Collector.objects.create(
            name="Poller-Remote",
            api_key_hash="remotehash_unique_1234567890123456789012345",
            status="active",
        )
        resp = auth_client.get("/api/collectors/?search=DC1")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()["results"]]
        assert "Poller-DC1" in names
        assert "Poller-Remote" not in names

    def test_search_by_remote_ip(self, auth_client, collector):
        resp = auth_client.get("/api/collectors/?search=10.10.0.50")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_api_key_hash_is_read_only(self, auth_client):
        # api_key_hash is in read_only_fields — values sent in POST are silently ignored
        resp = auth_client.post("/api/collectors/", {
            "name": "New Poller",
            "api_key_hash": "should-be-ignored",
        })
        assert resp.status_code == 201
        # The returned hash is empty string (not our submitted value) because field is read-only
        assert resp.json()["api_key_hash"] != "should-be-ignored"

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/collectors/")
        assert resp.status_code == 401


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestCollectorModel:
    def test_str(self, collector):
        assert str(collector) == "Poller-DC1"

    def test_status_choices(self):
        for val, _ in Collector.Status.choices:
            assert val in ("pending", "active", "offline", "revoked")

    def test_cert_serial_optional(self):
        c = Collector.objects.create(
            name="No Cert",
            api_key_hash="nocerthash_unique_1234567890123456789012345",
        )
        assert c.cert_serial == ""
        assert c.cert_expires_at is None

    def test_last_seen_at_nullable(self):
        c = Collector.objects.create(
            name="Never Seen",
            api_key_hash="neverseenhash_unique_1234567890123456789012",
        )
        assert c.last_seen_at is None

    def test_remote_ip_nullable(self):
        c = Collector.objects.create(
            name="No IP",
            api_key_hash="noiphash_unique_123456789012345678901234567",
        )
        assert c.remote_ip is None


# ── Telemetry Stub ────────────────────────────────────────────────────────────

class TestTelemetryStub:
    def test_metrics_returns_501(self, auth_client):
        resp = auth_client.get("/api/telemetry/metrics/")
        assert resp.status_code == 501

    def test_metrics_unauthenticated(self, api_client):
        resp = api_client.get("/api/telemetry/metrics/")
        assert resp.status_code == 401


class TestCollectorResolution:
    """effective_collector(_ip): device → site default → global default → setting."""

    def test_precedence(self, settings):
        from apps.collectors.models import Collector
        from apps.collectors.resolve import effective_collector, effective_collector_ip
        from apps.devices.models import Device, Site

        settings.COLLECTOR_IP = "9.9.9.9"
        glob = Collector.objects.create(name="Global", api_key_hash="h-glob", is_default=True, collector_ip="1.1.1.1")
        site_col = Collector.objects.create(name="SiteCol", api_key_hash="h-site", collector_ip="2.2.2.2")
        dev_col = Collector.objects.create(name="DevCol", api_key_hash="h-dev", collector_ip="3.3.3.3")
        site = Site.objects.create(name="DC", default_collector=site_col)
        d = Device.objects.create(hostname="r1", ip_address="10.0.0.1", site=site, collector=dev_col)

        # device collector wins
        assert effective_collector(d).name == "DevCol"
        assert effective_collector_ip(d) == "3.3.3.3"
        # fall back to site default
        d.collector = None; d.save()
        assert effective_collector_ip(d) == "2.2.2.2"
        # fall back to global default
        site.default_collector = None; site.save()
        assert effective_collector_ip(d) == "1.1.1.1"
        # fall back to the setting
        glob.is_default = False; glob.save()
        assert effective_collector_ip(d) == "9.9.9.9"

    def test_serializer_exposes_new_fields(self, auth_client, collector):
        resp = auth_client.get("/api/collectors/")
        row = resp.json()["results"][0]
        for key in ("collector_ip", "site", "site_name", "status", "last_seen_at", "device_count", "is_default"):
            assert key in row

    def test_serializer_exposes_type_and_capabilities(self, auth_client, collector):
        resp = auth_client.get("/api/collectors/")
        row = resp.json()["results"][0]
        for key in ("collector_type", "hostname", "location", "capabilities", "is_healthy"):
            assert key in row


class TestLocalCollectorRegistration:
    def test_register_creates_local_collector(self):
        from apps.collectors.management.commands.register_local_collector import (
            register_local_collector,
        )
        c = register_local_collector()
        assert c.collector_type == Collector.CollectorType.LOCAL
        assert c.status == Collector.Status.ACTIVE
        assert c.capabilities.get("snmp") is True
        assert c.last_seen_at is not None
        assert c.is_healthy is True
        # First collector → becomes the global default.
        assert c.is_default is True

    def test_register_is_idempotent(self):
        from apps.collectors.management.commands.register_local_collector import (
            register_local_collector,
        )
        first = register_local_collector()
        second = register_local_collector()
        assert first.pk == second.pk
        assert Collector.objects.filter(
            collector_type=Collector.CollectorType.LOCAL,
        ).count() == 1

    def test_register_does_not_steal_existing_default(self):
        from apps.collectors.management.commands.register_local_collector import (
            register_local_collector,
        )
        Collector.objects.create(
            name="Existing default", api_key_hash="existing-default-hash",
            is_default=True,
        )
        c = register_local_collector()
        assert c.is_default is False  # an explicit default already exists


class TestCollectorHealth:
    def test_is_healthy_recent_heartbeat(self):
        from django.utils import timezone
        c = Collector.objects.create(
            name="Fresh", api_key_hash="fresh-hash", status="active",
            last_seen_at=timezone.now(),
        )
        assert c.is_healthy is True

    def test_is_unhealthy_when_stale(self):
        from datetime import timedelta
        from django.utils import timezone
        c = Collector.objects.create(
            name="Stale", api_key_hash="stale-hash", status="active",
            last_seen_at=timezone.now() - timedelta(hours=1),
        )
        assert c.is_healthy is False

    def test_is_unhealthy_when_never_seen(self, collector):
        # The fixture collector has no last_seen_at.
        assert collector.is_healthy is False


class TestCollectorDevicesAction:
    def test_devices_action_lists_assigned(self, auth_client, collector):
        from apps.devices.models import Device
        Device.objects.create(
            hostname="edge-1", ip_address="10.9.9.9", vendor="Cisco",
            platform="ios_xe", collector=collector,
        )
        Device.objects.create(hostname="other", ip_address="10.9.9.10", vendor="Cisco", platform="ios_xe")
        resp = auth_client.get(f"/api/collectors/{collector.pk}/devices/")
        assert resp.status_code == 200
        hostnames = [d["hostname"] for d in resp.json()]
        assert hostnames == ["edge-1"]
