import hashlib
import hmac
import time

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db


class TestHealth:
    def test_health_returns_200_when_db_ok(self, api_client):
        resp = api_client.get("/api/health/")
        assert resp.status_code == 200

    def test_health_body(self, api_client):
        resp = api_client.get("/api/health/")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] is True

    def test_health_no_auth_required(self, api_client):
        resp = api_client.get("/api/health/")
        assert resp.status_code != 401

    def test_health_includes_setup_complete(self, api_client, monkeypatch):
        from apps.core import views
        monkeypatch.setattr(views, "_openbao_healthy", lambda: True)
        data = api_client.get("/api/health/").json()
        assert "setup_complete" in data
        assert data["openbao"] == "healthy"


class TestSetupStatus:
    def test_no_auth_required(self, api_client, monkeypatch):
        from apps.core import views
        monkeypatch.setattr(views, "_openbao_healthy", lambda: True)
        resp = api_client.get("/api/setup/status/")
        assert resp.status_code == 200

    def test_body_shape(self, api_client, monkeypatch, settings):
        from apps.core import views
        monkeypatch.setattr(views, "_openbao_healthy", lambda: True)
        monkeypatch.setattr(views, "_netpulse_version", lambda: "v1.2.3")
        settings.SETUP_COMPLETE = True
        data = api_client.get("/api/setup/status/").json()
        assert data["setup_complete"] is True
        assert data["openbao_healthy"] is True
        assert data["database_healthy"] is True
        assert data["version"] == "v1.2.3"

    def test_reflects_setup_incomplete(self, api_client, monkeypatch, settings):
        from apps.core import views
        monkeypatch.setattr(views, "_openbao_healthy", lambda: False)
        settings.SETUP_COMPLETE = False
        data = api_client.get("/api/setup/status/").json()
        assert data["setup_complete"] is False
        assert data["openbao_healthy"] is False


class TestChatOpsSlack:
    def _slack_headers(self, body: str, secret: str = "") -> dict:
        ts = str(int(time.time()))
        base = f"v0:{ts}:{body}"
        sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_SLACK_REQUEST_TIMESTAMP": ts, "HTTP_X_SLACK_SIGNATURE": sig}

    def test_url_verification_challenge(self, api_client):
        payload = {"type": "url_verification", "challenge": "abc123"}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123"

    def test_help_intent(self, api_client):
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_unknown_intent_returns_help(self, api_client):
        payload = {"event": {"text": "completely unknown command xyz", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_device_status_not_found(self, api_client):
        payload = {"event": {"text": "status of nonexistent-router", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "not found" in resp.json()["text"]

    def test_device_status_found(self, api_client):
        from apps.devices.models import Device
        Device.objects.create(hostname="core-sw-01", ip_address="10.0.0.1", vendor="Cisco")
        payload = {"event": {"text": "status of core-sw-01", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "core-sw-01" in resp.json()["text"]

    def test_active_alerts_none(self, api_client):
        payload = {"event": {"text": "any alerts right now", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "No active alerts" in resp.json()["text"]

    def test_active_alerts_found(self, api_client):
        from apps.alerts.models import AlertEvent, AlertRule
        rule = AlertRule.objects.create(
            name="CPU High", severity="critical", condition={"metric": "cpu", "threshold": 90}
        )
        AlertEvent.objects.create(rule=rule, state="firing")
        payload = {"event": {"text": "any alerts", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "active alert" in resp.json()["text"]

    def test_invalid_signature_rejected(self, api_client, monkeypatch):
        import os
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "real-secret")
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post(
            "/api/webhooks/slack/",
            data=payload,
            format="json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=str(int(time.time())),
            HTTP_X_SLACK_SIGNATURE="v0=badsignature",
        )
        assert resp.status_code == 401

    def test_no_auth_required(self, api_client):
        resp = api_client.post("/api/webhooks/slack/", data={}, format="json")
        assert resp.status_code != 401

    def test_bot_mention_stripped(self, api_client):
        payload = {"event": {"text": "<@U123BOT> help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_site_status_not_found(self, api_client):
        payload = {"event": {"text": "status of site dallas", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "not found" in resp.json()["text"]

    def test_site_status_found(self, api_client):
        from apps.devices.models import Device, Site
        site = Site.objects.create(name="Dallas")
        Device.objects.create(hostname="rtr-dal-01", ip_address="10.1.0.1", site=site, status="active")
        payload = {"event": {"text": "status of site Dallas", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "Dallas" in resp.json()["text"]


class TestChatOpsTeams:
    def test_teams_help(self, api_client):
        payload = {"text": "help", "from": {"name": "TestUser"}}
        resp = api_client.post("/api/webhooks/teams/", data=payload, format="json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert "spane commands" in data["text"]

    def test_teams_html_stripped(self, api_client):
        payload = {"text": "<p>help</p>", "from": {"name": "TestUser"}}
        resp = api_client.post("/api/webhooks/teams/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_teams_no_auth_required(self, api_client):
        resp = api_client.post("/api/webhooks/teams/", data={}, format="json")
        assert resp.status_code != 401


class TestChatOpsGChat:
    def test_gchat_help(self, api_client):
        payload = {"message": {"text": "help", "sender": {"displayName": "TestUser"}}}
        resp = api_client.post("/api/webhooks/gchat/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_gchat_no_auth_required(self, api_client):
        resp = api_client.post("/api/webhooks/gchat/", data={}, format="json")
        assert resp.status_code != 401


class TestChatOpsDiscord:
    def test_discord_help_via_content(self, api_client):
        payload = {"content": "help"}
        resp = api_client.post("/api/webhooks/discord/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["content"]

    def test_discord_help_via_options(self, api_client):
        payload = {"data": {"options": [{"value": "help"}]}}
        resp = api_client.post("/api/webhooks/discord/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["content"]

    def test_discord_no_auth_required(self, api_client):
        resp = api_client.post("/api/webhooks/discord/", data={}, format="json")
        assert resp.status_code != 401


class TestUnauthenticated:
    def test_api_devices_requires_auth(self, api_client):
        resp = api_client.get("/api/devices/")
        assert resp.status_code == 401

    def test_api_alerts_requires_auth(self, api_client):
        resp = api_client.get("/api/alerts/rules/")
        assert resp.status_code == 401
