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


class TestAutoResolution:
    def test_resolve_matching_by_labels(self, rule):
        from apps.alerts.models import AlertEvent
        from apps.alerts.resolve import resolve_matching
        e = AlertEvent.objects.create(rule=rule, state="firing",
                                      labels={"source": "reachability_monitor", "device_id": 7})
        other = AlertEvent.objects.create(rule=rule, state="firing",
                                          labels={"source": "reachability_monitor", "device_id": 8})
        n = resolve_matching(note="back up", source="reachability_monitor", device_id=7)
        assert n == 1
        e.refresh_from_db(); other.refresh_from_db()
        assert e.state == "resolved" and e.resolved_by == "auto" and e.resolution_note == "back up" and e.resolved_at
        assert other.state == "firing"  # different device untouched

    def test_list_defaults_to_active_only(self, auth_client, rule):
        from apps.alerts.models import AlertEvent
        AlertEvent.objects.create(rule=rule, state="firing")
        AlertEvent.objects.create(rule=rule, state="resolved")
        body = auth_client.get("/api/alerts/events/").json()
        assert body["count"] == 1
        assert all(e["state"] == "firing" for e in body["results"])

    def test_list_resolved_true(self, auth_client, rule):
        from apps.alerts.models import AlertEvent
        AlertEvent.objects.create(rule=rule, state="firing")
        AlertEvent.objects.create(rule=rule, state="resolved")
        body = auth_client.get("/api/alerts/events/?resolved=true").json()
        assert body["count"] == 1 and body["results"][0]["state"] == "resolved"

    def test_list_resolved_all(self, auth_client, rule):
        from apps.alerts.models import AlertEvent
        AlertEvent.objects.create(rule=rule, state="firing")
        AlertEvent.objects.create(rule=rule, state="resolved")
        assert auth_client.get("/api/alerts/events/?resolved=all").json()["count"] == 2

    def test_is_resolved_in_serializer(self, auth_client, rule):
        from apps.alerts.models import AlertEvent
        e = AlertEvent.objects.create(rule=rule, state="resolved")
        body = auth_client.get(f"/api/alerts/events/{e.pk}/").json()
        assert body["is_resolved"] is True

    def test_manual_resolve_action(self, auth_client, event):
        resp = auth_client.post(f"/api/alerts/events/{event.pk}/resolve/", {"note": "fixed by hand"}, format="json")
        assert resp.status_code == 200
        event.refresh_from_db()
        assert event.state == "resolved" and event.resolved_by == "user" and event.resolution_note == "fixed by hand"

    def test_purge_resolved_alerts(self, rule):
        from datetime import timedelta
        from django.utils import timezone
        from apps.alerts.models import AlertEvent
        from apps.alerts.management.commands.purge_resolved_alerts import purge_resolved_alerts
        old = AlertEvent.objects.create(rule=rule, state="resolved")
        AlertEvent.objects.filter(pk=old.pk).update(resolved_at=timezone.now() - timedelta(days=120))
        recent = AlertEvent.objects.create(rule=rule, state="resolved", resolved_at=timezone.now())
        firing = AlertEvent.objects.create(rule=rule, state="firing")
        assert purge_resolved_alerts(90) == 1
        assert not AlertEvent.objects.filter(pk=old.pk).exists()
        assert AlertEvent.objects.filter(pk=recent.pk).exists()
        assert AlertEvent.objects.filter(pk=firing.pk).exists()

    def test_reachability_recovery_auto_resolves(self, rule):
        # Firing reachability alert is auto-resolved when the device recovers.
        from apps.alerts.models import AlertEvent
        from apps.devices.models import Device
        from apps.devices.management.commands.run_reachability_monitor import Command
        d = Device.objects.create(hostname="r1", ip_address="10.0.0.1", status="unreachable", is_reachable=False)
        AlertEvent.objects.create(rule=rule, state="firing",
                                  labels={"source": "reachability_monitor", "device_id": d.id})
        cmd = Command()
        row = {"id": d.id, "hostname": d.hostname, "ip_address": d.ip_address,
               "status": "unreachable", "consecutive_failures": 5}
        cmd._apply_all([(row, True, "tcp", 2.5)])  # device came back up
        assert AlertEvent.objects.filter(state="firing").count() == 0
        assert AlertEvent.objects.get(state="resolved").resolved_by == "auto"

    def test_interface_recovery_auto_resolves(self, rule):
        from apps.alerts.models import AlertEvent
        from apps.alerts import interface_monitor
        from apps.devices.models import Device
        from apps.telemetry.models import MonitoredInterface
        from django.utils import timezone
        d = Device.objects.create(hostname="sw1", ip_address="10.0.0.2")
        iface = MonitoredInterface.objects.create(device=d, if_name="Gi0/1", last_status="down", alert_on_up=False)
        AlertEvent.objects.create(rule=rule, state="firing",
                                  labels={"source": "interface_monitor", "device_id": d.id, "interface": "Gi0/1"})
        # up alerts muted, but the firing down alert must still be resolved.
        interface_monitor.process_interface_status(iface, "up", timezone.now())
        assert AlertEvent.objects.get(labels__source="interface_monitor").state == "resolved"


# ── Bulk actions + state summary ─────────────────────────────────────────────

class TestBulkActions:
    def _events(self, rule, n, state="firing"):
        return [AlertEvent.objects.create(rule=rule, state=state, labels={"i": str(i)})
                for i in range(n)]

    def test_bulk_resolve(self, auth_client, rule):
        evs = self._events(rule, 3)
        ids = [e.id for e in evs]
        resp = auth_client.post("/api/alerts/events/bulk-resolve/",
                                {"ids": ids, "resolution_note": "maintenance"}, format="json")
        assert resp.status_code == 200
        assert resp.json() == {"updated": 3, "failed": 0, "errors": []}
        for e in evs:
            e.refresh_from_db()
            assert e.state == "resolved"
            assert e.resolved_by == "user"
            assert e.resolution_note == "maintenance"

    def test_bulk_resolve_skips_already_resolved(self, auth_client, rule):
        a = AlertEvent.objects.create(rule=rule, state="firing")
        b = AlertEvent.objects.create(rule=rule, state="resolved")
        resp = auth_client.post("/api/alerts/events/bulk-resolve/",
                                {"ids": [a.id, b.id]}, format="json")
        # b was already resolved → only a counts as updated.
        assert resp.json()["updated"] == 1
        assert resp.json()["failed"] == 1

    def test_bulk_resolve_no_ids(self, auth_client):
        resp = auth_client.post("/api/alerts/events/bulk-resolve/", {"ids": []}, format="json")
        assert resp.status_code == 400

    def test_bulk_acknowledge(self, auth_client, rule):
        evs = self._events(rule, 2)
        ids = [e.id for e in evs]
        resp = auth_client.post("/api/alerts/events/bulk-acknowledge/",
                                {"ids": ids, "note": "looking"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        from apps.alerting.models import AlertAcknowledgement
        assert AlertAcknowledgement.objects.filter(alert_event__in=ids).count() == 2

    def test_acknowledged_state_filter_and_summary(self, auth_client, rule):
        firing = AlertEvent.objects.create(rule=rule, state="firing")
        acked = AlertEvent.objects.create(rule=rule, state="firing")
        AlertEvent.objects.create(rule=rule, state="resolved")
        # Acknowledge one firing event.
        auth_client.post("/api/alerts/events/bulk-acknowledge/", {"ids": [acked.id]}, format="json")

        summary = auth_client.get("/api/alerts/events/summary/").json()
        assert summary == {"all": 3, "firing": 1, "acknowledged": 1, "resolved": 1}

        # ?state=acknowledged returns only the acked firing event.
        ack_list = auth_client.get("/api/alerts/events/?state=acknowledged").json()
        ids = [r["id"] for r in ack_list["results"]]
        assert ids == [acked.id]
        assert ack_list["results"][0]["is_acknowledged"] is True

        # ?state=firing excludes the acknowledged one.
        firing_list = auth_client.get("/api/alerts/events/?state=firing").json()
        assert [r["id"] for r in firing_list["results"]] == [firing.id]
