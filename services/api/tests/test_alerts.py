import pytest
from apps.alerts.models import AlertChannel, AlertEvent, AlertRule

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def channel():
    return AlertChannel.objects.create(
        name="Slack Ops",
        channel_type="slack",
        config={"webhook_url": "https://hooks.slack.com/test"},
    )


@pytest.fixture
def rule(channel):
    r = AlertRule.objects.create(
        name="High CPU",
        severity="critical",
        condition={"metric": "cpu_utilization", "threshold": 90},
    )
    r.channels.add(channel)
    return r


@pytest.fixture
def event(rule):
    return AlertEvent.objects.create(rule=rule, state="firing", labels={"device": "rtr-01"})


# ── AlertChannel ──────────────────────────────────────────────────────────────

class TestAlertChannelEndpoints:
    def test_list_channels(self, auth_client, channel):
        resp = auth_client.get("/api/alerts/channels/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_channel(self, auth_client):
        resp = auth_client.post("/api/alerts/channels/", {
            "name": "PD On-Call",
            "channel_type": "pagerduty",
            "config": {"routing_key": "abc123"},
            "is_active": True,
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["channel_type"] == "pagerduty"

    def test_retrieve_channel(self, auth_client, channel):
        resp = auth_client.get(f"/api/alerts/channels/{channel.pk}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Slack Ops"

    def test_update_channel(self, auth_client, channel):
        resp = auth_client.patch(f"/api/alerts/channels/{channel.pk}/", {"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_channel(self, auth_client, channel):
        resp = auth_client.delete(f"/api/alerts/channels/{channel.pk}/")
        assert resp.status_code == 204
        assert not AlertChannel.objects.filter(pk=channel.pk).exists()

    def test_filter_by_channel_type(self, auth_client, channel):
        AlertChannel.objects.create(name="Email Ops", channel_type="email", config={})
        resp = auth_client.get("/api/alerts/channels/?channel_type=slack")
        assert resp.status_code == 200
        assert all(c["channel_type"] == "slack" for c in resp.json()["results"])

    def test_filter_by_is_active(self, auth_client, channel):
        AlertChannel.objects.create(name="Inactive", channel_type="email", config={}, is_active=False)
        resp = auth_client.get("/api/alerts/channels/?is_active=true")
        assert resp.status_code == 200
        assert all(c["is_active"] is True for c in resp.json()["results"])

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/alerts/channels/")
        assert resp.status_code == 401


# ── AlertRule ─────────────────────────────────────────────────────────────────

class TestAlertRuleEndpoints:
    def test_list_rules(self, auth_client, rule):
        resp = auth_client.get("/api/alerts/rules/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_rule(self, auth_client):
        resp = auth_client.post("/api/alerts/rules/", {
            "name": "Interface Down",
            "severity": "high",
            "condition": {"metric": "if_oper_status", "value": "down"},
            "is_active": True,
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["severity"] == "high"

    def test_create_rule_default_severity(self, auth_client):
        resp = auth_client.post("/api/alerts/rules/", {
            "name": "Temp Rule",
            "condition": {},
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["severity"] == "medium"

    def test_retrieve_rule(self, auth_client, rule):
        resp = auth_client.get(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "High CPU"

    def test_update_rule_severity(self, auth_client, rule):
        resp = auth_client.patch(f"/api/alerts/rules/{rule.pk}/", {"severity": "high"})
        assert resp.status_code == 200
        assert resp.json()["severity"] == "high"

    def test_delete_rule(self, auth_client, rule):
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 204

    def test_filter_by_severity(self, auth_client, rule):
        AlertRule.objects.create(name="Low Disk", severity="low", condition={})
        resp = auth_client.get("/api/alerts/rules/?severity=critical")
        assert resp.status_code == 200
        assert all(r["severity"] == "critical" for r in resp.json()["results"])

    def test_filter_by_is_active(self, auth_client, rule):
        AlertRule.objects.create(name="Inactive Rule", severity="info", condition={}, is_active=False)
        resp = auth_client.get("/api/alerts/rules/?is_active=true")
        assert resp.status_code == 200
        assert all(r["is_active"] is True for r in resp.json()["results"])

    def test_search_by_name(self, auth_client, rule):
        AlertRule.objects.create(name="Disk Space Low", severity="low", condition={})
        resp = auth_client.get("/api/alerts/rules/?search=CPU")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()["results"]]
        assert "High CPU" in names
        assert "Disk Space Low" not in names


# ── AlertEvent ────────────────────────────────────────────────────────────────

class TestAlertEventEndpoints:
    def test_list_events(self, auth_client, event):
        resp = auth_client.get("/api/alerts/events/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_retrieve_event(self, auth_client, event):
        resp = auth_client.get(f"/api/alerts/events/{event.pk}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "firing"
        assert data["rule_name"] == "High CPU"
        assert data["severity"] == "critical"

    def test_event_no_create_endpoint(self, auth_client, rule):
        resp = auth_client.post("/api/alerts/events/", {
            "rule": rule.pk, "state": "firing", "labels": {}
        }, format="json")
        assert resp.status_code == 405

    def test_event_no_delete_endpoint(self, auth_client, event):
        resp = auth_client.delete(f"/api/alerts/events/{event.pk}/")
        assert resp.status_code == 405

    def test_update_event_to_resolved(self, auth_client, event):
        from django.utils import timezone
        resp = auth_client.patch(f"/api/alerts/events/{event.pk}/", {
            "state": "resolved",
            "resolved_at": timezone.now().isoformat(),
        }, format="json")
        assert resp.status_code == 200
        assert resp.json()["state"] == "resolved"

    def test_filter_events_by_state(self, auth_client, event, rule):
        AlertEvent.objects.create(rule=rule, state="resolved")
        resp = auth_client.get("/api/alerts/events/?state=firing")
        assert resp.status_code == 200
        assert all(e["state"] == "firing" for e in resp.json()["results"])

    def test_filter_events_by_severity(self, auth_client, event):
        low_rule = AlertRule.objects.create(name="Low Disk", severity="low", condition={})
        AlertEvent.objects.create(rule=low_rule, state="firing")
        resp = auth_client.get("/api/alerts/events/?rule__severity=critical")
        assert resp.status_code == 200
        assert all(e["severity"] == "critical" for e in resp.json()["results"])

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/alerts/events/")
        assert resp.status_code == 401


class TestInterfaceAlertSerialization:
    """The event serializer surfaces interface metadata from labels/annotations."""

    def test_interface_down_fields(self, auth_client, rule):
        e = AlertEvent.objects.create(
            rule=rule, state="firing",
            labels={"source": "interface_monitor", "device": "rtr-1", "device_id": 7,
                    "interface": "GigabitEthernet1", "severity": "high", "transition": "down"},
            annotations={"title": "Interface Down: rtr-1 GigabitEthernet1",
                         "message": "GigabitEthernet1 on rtr-1 changed from up to down",
                         "severity": "high", "downtime_seconds": None},
        )
        data = auth_client.get(f"/api/alerts/events/{e.pk}/").json()
        assert data["is_interface_alert"] is True
        assert data["interface"] == "GigabitEthernet1"
        assert data["transition"] == "down"
        assert data["device"] == "rtr-1" and data["device_id"] == 7
        assert data["effective_severity"] == "high"
        assert "down" in data["message"]
        assert data["fired_at"]  # mapped from created_at

    def test_interface_recovery_reports_info_and_downtime(self, auth_client, rule):
        e = AlertEvent.objects.create(
            rule=rule, state="firing",
            labels={"source": "interface_monitor", "device": "rtr-1", "device_id": 7,
                    "interface": "Gi1", "severity": "info", "transition": "up"},
            annotations={"title": "Interface Recovered", "message": "Gi1 is back up",
                         "severity": "info", "downtime_seconds": 142},
        )
        data = auth_client.get(f"/api/alerts/events/{e.pk}/").json()
        # rule severity is unchanged (critical), but the event's effective is info
        assert data["severity"] == "critical"
        assert data["effective_severity"] == "info"
        assert data["transition"] == "up" and data["downtime_seconds"] == 142

    def test_ordinary_alert_has_no_interface_flag(self, auth_client, event):
        data = auth_client.get(f"/api/alerts/events/{event.pk}/").json()
        assert data["is_interface_alert"] is False
        assert data["effective_severity"] == "critical"  # falls back to rule severity


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestAlertModels:
    def test_alert_rule_str(self, rule):
        assert str(rule) == "High CPU"

    def test_alert_channel_str(self, channel):
        assert "Slack Ops" in str(channel)
        assert "slack" in str(channel)

    def test_event_default_state_firing(self, rule):
        e = AlertEvent.objects.create(rule=rule, labels={})
        assert e.state == "firing"

    def test_cooldown_default(self, auth_client):
        resp = auth_client.post("/api/alerts/rules/", {
            "name": "Default Cooldown",
            "condition": {},
        }, format="json")
        assert resp.json()["cooldown_minutes"] == 60
