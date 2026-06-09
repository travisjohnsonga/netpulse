"""Remote-collector enrollment, heartbeat auth, and offline sweep."""
import pytest
from django.utils import timezone

from apps.collectors import auth
from apps.collectors.models import Collector, HEARTBEAT_HEALTHY_SECONDS
from apps.devices.models import Site

pytestmark = pytest.mark.django_db


class TestAuthHelpers:
    def test_hash_and_verify(self):
        key = auth.generate_api_key()
        assert key.startswith("npc_")
        h = auth.hash_secret(key)
        assert h != key and auth.verify_secret(key, h)
        assert not auth.verify_secret("wrong", h)

    def test_verify_is_safe_on_garbage(self):
        assert auth.verify_secret("", "") is False
        assert auth.verify_secret("x", "not-a-bcrypt-hash") is False


class TestEnrollment:
    def test_create_returns_one_time_token(self, auth_client):
        resp = auth_client.post("/api/collectors/", {"name": "edge-1"}, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["enrollment_token"].startswith("npe_")
        assert body["status"] == "pending" and body["collector_type"] == "remote"
        c = Collector.objects.get(pk=body["id"])
        # Token stored only as a hash; api_key is a placeholder until enroll.
        assert c.enrollment_token_hash and c.enrollment_token_hash != body["enrollment_token"]
        assert c.api_key_hash.startswith("pending-")

    def test_enroll_exchanges_token_for_api_key(self, auth_client, api_client):
        token = auth_client.post("/api/collectors/", {"name": "edge-2"}, format="json").json()["enrollment_token"]
        resp = api_client.post("/api/collectors/enroll/", {
            "enrollment_token": token, "hostname": "edge2.local",
            "capabilities": {"snmp": True}, "version": "1.0.0"}, format="json")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["api_key"].startswith("npc_")
        assert body["nats_account"] == f"collector-{body['collector_id']}"
        assert body["cert_issued"] is False  # OpenBao disabled in tests
        c = Collector.objects.get(pk=body["collector_id"])
        assert c.enrolled_at is not None and c.enrollment_token_hash == ""
        assert c.hostname == "edge2.local" and c.capabilities == {"snmp": True}
        # The issued key verifies against the stored hash.
        assert auth.verify_secret(body["api_key"], c.api_key_hash)

    def test_enroll_token_is_single_use(self, auth_client, api_client):
        token = auth_client.post("/api/collectors/", {"name": "edge-3"}, format="json").json()["enrollment_token"]
        assert api_client.post("/api/collectors/enroll/", {"enrollment_token": token}, format="json").status_code == 200
        # Reusing the same token now fails.
        assert api_client.post("/api/collectors/enroll/", {"enrollment_token": token}, format="json").status_code == 401

    def test_enroll_rejects_bad_token(self, api_client):
        assert api_client.post("/api/collectors/enroll/", {"enrollment_token": "npe_bogus"}, format="json").status_code == 401

    def test_regenerate_token_reenables_enroll(self, auth_client, api_client):
        cid = auth_client.post("/api/collectors/", {"name": "edge-4"}, format="json").json()["id"]
        # Enroll once, then re-issue a token and enroll again.
        t1 = auth_client.post("/api/collectors/", {"name": "edge-4b"}, format="json").json()["enrollment_token"]
        api_client.post("/api/collectors/enroll/", {"enrollment_token": t1}, format="json")
        t2 = auth_client.post(f"/api/collectors/{cid}/regenerate-token/").json()["enrollment_token"]
        assert api_client.post("/api/collectors/enroll/", {"enrollment_token": t2}, format="json").status_code == 200


class TestHeartbeat:
    def _enroll(self, auth_client, api_client, name="hb"):
        token = auth_client.post("/api/collectors/", {"name": name}, format="json").json()["enrollment_token"]
        body = api_client.post("/api/collectors/enroll/", {"enrollment_token": token}, format="json").json()
        return body["collector_id"], body["api_key"]

    def test_heartbeat_marks_active(self, auth_client, api_client):
        cid, key = self._enroll(auth_client, api_client)
        resp = api_client.post("/api/collectors/heartbeat/", {"version": "1.2.3"}, format="json",
                               HTTP_X_COLLECTOR_ID=str(cid), HTTP_X_COLLECTOR_KEY=key)
        assert resp.status_code == 200 and resp.json()["status"] == "active"
        c = Collector.objects.get(pk=cid)
        assert c.status == "active" and c.last_seen_at is not None and c.version == "1.2.3"

    def test_heartbeat_rejects_bad_key(self, auth_client, api_client):
        cid, _ = self._enroll(auth_client, api_client, name="hb2")
        resp = api_client.post("/api/collectors/heartbeat/", {}, format="json",
                               HTTP_X_COLLECTOR_ID=str(cid), HTTP_X_COLLECTOR_KEY="npc_wrong")
        assert resp.status_code == 401

    def test_revoked_collector_cannot_heartbeat(self, auth_client, api_client):
        cid, key = self._enroll(auth_client, api_client, name="hb3")
        auth_client.post(f"/api/collectors/{cid}/revoke/")
        resp = api_client.post("/api/collectors/heartbeat/", {}, format="json",
                               HTTP_X_COLLECTOR_ID=str(cid), HTTP_X_COLLECTOR_KEY=key)
        assert resp.status_code == 401


class TestOfflineSweep:
    def test_sweep_flips_stale_remote_active_to_offline(self):
        from apps.core.management.commands.run_scheduler import Command
        old = timezone.now() - timezone.timedelta(seconds=HEARTBEAT_HEALTHY_SECONDS + 60)
        stale = Collector.objects.create(name="stale", collector_type="remote", status="active",
                                         last_seen_at=old, api_key_hash="k-stale")
        fresh = Collector.objects.create(name="fresh", collector_type="remote", status="active",
                                         last_seen_at=timezone.now(), api_key_hash="k-fresh")
        local = Collector.objects.create(name="local", collector_type="local", status="active",
                                         last_seen_at=old, api_key_hash="k-local")
        Command()._collector_offline_sweep()
        stale.refresh_from_db(); fresh.refresh_from_db(); local.refresh_from_db()
        assert stale.status == "offline"
        assert fresh.status == "active"
        assert local.status == "active"  # local collectors are never swept


class TestServedSites:
    def test_served_sites_reflect_default_collector(self, auth_client):
        # Site.default_collector is the single assignment authority; the collector
        # serializer reports the served sites read-only via default_for_sites.
        cid = auth_client.post("/api/collectors/", {"name": "edge-sites"}, format="json").json()["id"]
        c = Collector.objects.get(pk=cid)
        s1 = Site.objects.create(name="DC-A", default_collector=c)
        s2 = Site.objects.create(name="DC-B", default_collector=c)
        Site.objects.create(name="DC-C")  # not served by this collector
        body = auth_client.get(f"/api/collectors/{cid}/").json()
        assert set(body["assigned_site_ids"]) == {s1.id, s2.id}
        assert set(body["assigned_site_names"]) == {"DC-A", "DC-B"}
