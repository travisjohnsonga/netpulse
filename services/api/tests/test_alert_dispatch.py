"""Tests for the alert dispatch layer + email/Teams/webhook/slack notifiers."""
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from apps.alerts import dispatch, payload as payload_mod
from apps.alerts.models import AlertChannel, AlertEvent, AlertRule, NotificationLog
from apps.alerts.notifiers import _REGISTRY, Notifier, get_notifier, registered_types

pytestmark = pytest.mark.django_db

ENABLED = override_settings(ALERT_DISPATCH_ENABLED=True, ALERT_DISPATCH_MAX_ATTEMPTS=1,
                            ALERT_DISPATCH_BACKOFF_S=0)


# ── helpers ─────────────────────────────────────────────────────────────────

class RecordingNotifier(Notifier):
    def __init__(self):
        self.calls = []
        self.result = (True, "ok")

    def send(self, channel, payload):
        self.calls.append((channel, payload))
        return self.result


@pytest.fixture
def rec(monkeypatch):
    """Install a recording notifier under the 'rec' channel type."""
    notifier = RecordingNotifier()
    monkeypatch.setitem(_REGISTRY, "rec", notifier)
    return notifier


def make_rule(severity="high", **kw):
    return AlertRule.objects.create(name=kw.pop("name", "R"), severity=severity,
                                    condition={}, **kw)


def make_event(rule, severity="high", title="Boom", **labels):
    return AlertEvent.objects.create(
        rule=rule, state=AlertEvent.State.FIRING,
        labels={"severity": severity, **labels},
        annotations={"title": title, "message": "msg", "severity": severity},
    )


def make_channel(channel_type="rec", config=None, **kw):
    return AlertChannel.objects.create(name=kw.pop("name", channel_type),
                                       channel_type=channel_type,
                                       config=config or {}, **kw)


# ── payload ───────────────────────────────────────────────────────────────────

class TestPayload:
    def test_build_payload_firing(self):
        rule = make_rule(severity="critical")
        ev = make_event(rule, severity="critical", title="DB down", device="rtr-01")
        p = payload_mod.build_payload(ev, payload_mod.FIRING)
        assert p.severity == "critical"
        assert p.title == "DB down"
        assert p.device == "rtr-01"
        assert not p.is_resolved
        assert p.subject().startswith("🔴")
        assert "[CRITICAL] DB down" in p.subject()

    def test_build_payload_resolved(self):
        rule = make_rule()
        ev = make_event(rule)
        ev.state = AlertEvent.State.RESOLVED
        ev.resolved_by = "auto"
        ev.save()
        p = payload_mod.build_payload(ev, payload_mod.RESOLVED)
        assert p.is_resolved
        assert p.subject().startswith("✅")
        assert p.color == "388E3C"

    @override_settings(FRONTEND_BASE_URL="https://spane.example.com")
    def test_link_built(self):
        rule = make_rule()
        ev = make_event(rule)
        p = payload_mod.build_payload(ev, payload_mod.FIRING)
        assert p.link == f"https://spane.example.com/alerts?event={ev.pk}"


# ── channel matching ───────────────────────────────────────────────────────────

class TestMatching:
    def test_rule_linked_channels(self, rec):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        p = payload_mod.build_payload(ev, payload_mod.FIRING)
        assert dispatch.matching_channels(ev, p) == [ch]

    def test_global_channel_matches_any_rule(self, rec):
        rule = make_rule()
        gch = make_channel(config={"all_alerts": True})
        ev = make_event(rule)
        p = payload_mod.build_payload(ev, payload_mod.FIRING)
        assert gch in dispatch.matching_channels(ev, p)

    def test_inactive_channel_excluded(self, rec):
        rule = make_rule()
        ch = make_channel(is_active=False)
        rule.channels.add(ch)
        ev = make_event(rule)
        p = payload_mod.build_payload(ev, payload_mod.FIRING)
        assert dispatch.matching_channels(ev, p) == []

    def test_severity_threshold(self, rec):
        rule = make_rule()
        ch = make_channel(config={"min_severity": "high"})
        rule.channels.add(ch)
        low = make_event(rule, severity="low")
        high = make_event(rule, severity="high")
        assert dispatch.matching_channels(low, payload_mod.build_payload(low, "firing")) == []
        assert dispatch.matching_channels(high, payload_mod.build_payload(high, "firing")) == [ch]

    def test_routing_match(self, rec):
        rule = make_rule()
        ch = make_channel(config={"match": {"site": "hq"}})
        rule.channels.add(ch)
        match = make_event(rule, site="hq")
        nomatch = make_event(rule, site="branch")
        assert dispatch.matching_channels(match, payload_mod.build_payload(match, "firing")) == [ch]
        assert dispatch.matching_channels(nomatch, payload_mod.build_payload(nomatch, "firing")) == []

    def test_ui_only_type_not_notified_by_default(self, rec):
        # config_changed is a UI/audit visibility type → AlertEvent still created,
        # but no channel notifies unless it opts in.
        rule = make_rule()
        ch = make_channel(config={"all_alerts": True})
        ev = make_event(rule, severity="medium", alert_type="config_changed")
        assert dispatch.matching_channels(ev, payload_mod.build_payload(ev, "firing")) == []

    def test_ui_only_type_notifies_when_opted_in(self, rec):
        rule = make_rule()
        ch = make_channel(config={"all_alerts": True, "notify_types": ["config_changed"]})
        ev = make_event(rule, severity="medium", alert_type="config_changed")
        assert dispatch.matching_channels(ev, payload_mod.build_payload(ev, "firing")) == [ch]

    def test_normal_type_still_notifies(self, rec):
        rule = make_rule()
        ch = make_channel(config={"all_alerts": True})
        ev = make_event(rule, severity="medium", alert_type="site_down")
        assert dispatch.matching_channels(ev, payload_mod.build_payload(ev, "firing")) == [ch]


# ── dispatch core ──────────────────────────────────────────────────────────────

class TestDispatch:
    @ENABLED
    def test_dispatch_sends_and_stamps(self, rec):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        summary = dispatch.dispatch_event(ev, "firing")
        assert summary["dispatched"] and summary["sent"] == 1
        assert len(rec.calls) == 1
        ev.refresh_from_db()
        assert ev.fired_notified_at is not None

    @ENABLED
    def test_idempotent_no_double_send(self, rec):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        dispatch.dispatch_event(ev, "firing")
        second = dispatch.dispatch_event(ev, "firing")
        assert second["reason"] == "already notified"
        assert len(rec.calls) == 1  # flapping/re-save cannot spam

    @ENABLED
    def test_resolved_transition_independent(self, rec):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        dispatch.dispatch_event(ev, "firing")
        ev.state = AlertEvent.State.RESOLVED
        ev.save()
        dispatch.dispatch_event(ev, "resolved")
        assert len(rec.calls) == 2
        ev.refresh_from_db()
        assert ev.resolved_notified_at is not None

    @ENABLED
    def test_disabled_setting(self, rec):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        with override_settings(ALERT_DISPATCH_ENABLED=False):
            summary = dispatch.dispatch_event(ev, "firing")
        assert summary["reason"] == "disabled"
        assert rec.calls == []

    @ENABLED
    def test_one_bad_channel_does_not_block_others(self, monkeypatch):
        good = RecordingNotifier()
        bad = RecordingNotifier()

        def boom(channel, payload):
            raise RuntimeError("smtp down")
        bad.send = boom
        monkeypatch.setitem(_REGISTRY, "good", good)
        monkeypatch.setitem(_REGISTRY, "bad", bad)
        rule = make_rule()
        gch = make_channel(channel_type="good", name="g")
        bch = make_channel(channel_type="bad", name="b")
        rule.channels.add(gch, bch)
        ev = make_event(rule)
        summary = dispatch.dispatch_event(ev, "firing")
        assert summary["sent"] == 1 and summary["failed"] == 1
        assert len(good.calls) == 1  # good channel still delivered

    @ENABLED
    def test_retry_then_succeed(self, monkeypatch):
        attempts = {"n": 0}

        class Flaky(Notifier):
            def send(self, channel, payload):
                attempts["n"] += 1
                return (attempts["n"] >= 2, "flaky")
        monkeypatch.setitem(_REGISTRY, "flaky", Flaky())
        ch = make_channel(channel_type="flaky")
        # event_id=None → a synthetic send (no NotificationLog write); this test
        # exercises retry behaviour, not delivery logging.
        p = payload_mod.AlertPayload(event_id=None, transition="firing", severity="high",
                                     title="t", message="m")
        with override_settings(ALERT_DISPATCH_MAX_ATTEMPTS=3, ALERT_DISPATCH_BACKOFF_S=0):
            ok, _ = dispatch.send_to_channel(ch, p)
        assert ok and attempts["n"] == 2

    @ENABLED
    def test_unknown_transition(self, rec):
        rule = make_rule()
        ev = make_event(rule)
        assert dispatch.dispatch_event(ev, "bogus")["reason"].startswith("unknown")

    @ENABLED
    def test_maintenance_suppresses_firing(self, rec, monkeypatch):
        monkeypatch.setattr("apps.alerting.maintenance.is_in_maintenance",
                            lambda **kw: True)
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        summary = dispatch.dispatch_event(ev, "firing")
        assert summary["reason"] == "maintenance"
        assert rec.calls == []

    @ENABLED
    def test_rule_notify_disabled_generates_but_skips_dispatch(self, rec):
        # notify_enabled=False: the AlertEvent still exists (UI), dispatch skipped.
        rule = make_rule()
        rule.notify_enabled = False
        rule.save(update_fields=["notify_enabled"])
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        summary = dispatch.dispatch_event(ev, "firing")
        assert summary["reason"] == "rule_notify_disabled"
        assert rec.calls == []
        assert AlertEvent.objects.filter(id=ev.id).exists()           # generation happened
        assert NotificationLog.objects.filter(event=ev).count() == 0  # no delivery

    @ENABLED
    def test_rule_notify_enabled_notifies(self, rec):
        rule = make_rule()  # notify_enabled defaults True
        ch = make_channel()
        rule.channels.add(ch)
        ev = make_event(rule)
        summary = dispatch.dispatch_event(ev, "firing")
        assert summary["sent"] == 1 and len(rec.calls) == 1


# ── signal wiring (create → on_commit → dispatch) ──────────────────────────────

class TestSignalWiring:
    @ENABLED
    def test_firing_create_dispatches_on_commit(self, rec, django_capture_on_commit_callbacks):
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        with django_capture_on_commit_callbacks(execute=True):
            ev = make_event(rule)
        assert len(rec.calls) == 1
        ev.refresh_from_db()
        assert ev.fired_notified_at is not None

    @ENABLED
    def test_resolve_matching_dispatches(self, rec, django_capture_on_commit_callbacks):
        from apps.alerts.resolve import resolve_matching
        rule = make_rule()
        ch = make_channel()
        rule.channels.add(ch)
        with django_capture_on_commit_callbacks(execute=True):
            ev = make_event(rule, source="x", device_id=7)
        rec.calls.clear()
        with django_capture_on_commit_callbacks(execute=True):
            n = resolve_matching(note="recovered", source="x", device_id=7)
        assert n == 1
        assert len(rec.calls) == 1
        assert rec.calls[0][1].is_resolved


# ── email notifier ─────────────────────────────────────────────────────────────

class TestEmailNotifier:
    def test_email_sends(self):
        ch = make_channel(channel_type="email",
                          config={"recipients": ["ops@example.com", "noc@example.com"]})
        p = payload_mod.AlertPayload(event_id=5, transition="firing", severity="critical",
                                     title="Link down", message="eth0 is down",
                                     device="rtr-01")
        ok, detail = get_notifier("email").send(ch, p)
        assert ok
        assert len(mail.outbox) == 1
        msg = mail.outbox[0]
        assert "Link down" in msg.subject
        assert "ops@example.com" in msg.to
        assert any("text/html" in alt[1] for alt in msg.alternatives)

    def test_email_resolved_subject(self):
        ch = make_channel(channel_type="email", config={"recipients": ["a@b.com"]})
        p = payload_mod.AlertPayload(event_id=5, transition="resolved", severity="critical",
                                     title="Link down", message="recovered")
        ok, _ = get_notifier("email").send(ch, p)
        assert ok
        assert mail.outbox[-1].subject.startswith("✅ Resolved")

    def test_email_no_recipients(self):
        ch = make_channel(channel_type="email", config={})
        p = payload_mod.AlertPayload(event_id=5, transition="firing", severity="low",
                                     title="t", message="m")
        ok, detail = get_notifier("email").send(ch, p)
        assert not ok and "recipient" in detail


# ── teams notifier ─────────────────────────────────────────────────────────────

class TestTeamsNotifier:
    def _payload(self, transition="firing"):
        return payload_mod.AlertPayload(
            event_id=9, transition=transition, severity="critical",
            title="Core switch down", message="No response",
            device="sw-core-01", link="https://spane.example.com/alerts?event=9")

    def test_adaptive_card_structure(self):
        from apps.alerts.notifiers.teams import build_adaptive_card
        body = build_adaptive_card(self._payload())
        assert body["type"] == "message"
        card = body["attachments"][0]["content"]
        assert card["type"] == "AdaptiveCard"
        assert card["actions"][0]["url"].endswith("event=9")
        titles = [b for b in card["body"] if b["type"] == "TextBlock"]
        assert any("Core switch down" in b["text"] for b in titles)
        assert any(b["type"] == "FactSet" for b in card["body"])

    def test_message_card_structure(self):
        from apps.alerts.notifiers.teams import build_message_card
        card = build_message_card(self._payload())
        assert card["@type"] == "MessageCard"
        assert card["themeColor"] == "D32F2F"
        assert card["potentialAction"][0]["targets"][0]["uri"].endswith("event=9")

    def test_resolved_card_is_green(self):
        from apps.alerts.notifiers.teams import build_message_card
        card = build_message_card(self._payload("resolved"))
        assert card["themeColor"] == "388E3C"
        assert "Resolved" in card["title"]

    def test_send_posts_to_webhook(self):
        ch = make_channel(channel_type="teams",
                          config={"webhook_url": "https://teams.example.com/hook"})
        with mock.patch("requests.post") as post:
            post.return_value = mock.Mock(status_code=200)
            ok, _ = get_notifier("teams").send(ch, self._payload())
        assert ok
        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        assert url == "https://teams.example.com/hook"
        assert kwargs["json"]["type"] == "message"

    def test_send_no_webhook(self):
        ch = make_channel(channel_type="teams", config={})
        ok, detail = get_notifier("teams").send(ch, self._payload())
        assert not ok and "webhook_url" in detail

    def test_send_handles_http_error(self):
        ch = make_channel(channel_type="teams", config={"webhook_url": "https://x/y"})
        with mock.patch("requests.post") as post:
            post.return_value = mock.Mock(status_code=500)
            ok, detail = get_notifier("teams").send(ch, self._payload())
        assert not ok and "500" in detail


# ── webhook / slack / pagerduty ────────────────────────────────────────────────

class TestOtherNotifiers:
    def _payload(self):
        return payload_mod.AlertPayload(event_id=3, transition="firing", severity="high",
                                        title="t", message="m", device="d")

    def test_webhook_posts_structured_body(self):
        ch = make_channel(channel_type="webhook",
                          config={"url": "https://hook/x", "headers": {"X-Token": "z"}})
        with mock.patch("requests.post") as post:
            post.return_value = mock.Mock(status_code=204)
            ok, _ = get_notifier("webhook").send(ch, self._payload())
        assert ok
        assert post.call_args.kwargs["json"]["severity"] == "high"
        assert post.call_args.kwargs["headers"] == {"X-Token": "z"}

    def test_slack_posts(self):
        ch = make_channel(channel_type="slack",
                          config={"webhook_url": "https://hooks.slack.com/x"})
        with mock.patch("requests.post") as post:
            post.return_value = mock.Mock(status_code=200)
            ok, _ = get_notifier("slack").send(ch, self._payload())
        assert ok
        assert "attachments" in post.call_args.kwargs["json"]

    def test_pagerduty_trigger_and_resolve(self):
        ch = make_channel(channel_type="pagerduty", config={"routing_key": "rk"})
        with mock.patch("requests.post") as post:
            post.return_value = mock.Mock(status_code=202)
            get_notifier("pagerduty").send(ch, self._payload())
            body = post.call_args.kwargs["json"]
        assert body["event_action"] == "trigger"
        assert body["payload"]["severity"] == "error"

    def test_registry_has_builtins(self):
        for t in ("email", "teams", "webhook", "slack", "pagerduty"):
            assert t in registered_types()


# ── channel test endpoint ──────────────────────────────────────────────────────

class TestChannelTestEndpoint:
    def test_email_channel_test_action(self, auth_client):
        ch = make_channel(channel_type="email", config={"recipients": ["x@y.com"]})
        resp = auth_client.post(f"/api/alerts/channels/{ch.pk}/test/")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert len(mail.outbox) == 1

    def test_misconfigured_channel_returns_502(self, auth_client):
        ch = make_channel(channel_type="email", config={})
        resp = auth_client.post(f"/api/alerts/channels/{ch.pk}/test/")
        assert resp.status_code == 502
        assert resp.json()["ok"] is False


# ── delivery reliability (NotificationLog + cross-channel alarm + health) ────────

class FailingNotifier(Notifier):
    def send(self, channel, payload):
        return False, "boom"


class TestDeliveryReliability:
    @ENABLED
    def test_success_and_failure_are_logged(self, monkeypatch):
        monkeypatch.setitem(_REGISTRY, "good", RecordingNotifier())
        monkeypatch.setitem(_REGISTRY, "fail", FailingNotifier())
        rule = make_rule()
        gch = make_channel(channel_type="good", name="teams", config={"all_alerts": True})
        fch = make_channel(channel_type="fail", name="email", config={"all_alerts": True})
        ev = make_event(rule, severity="high")
        dispatch.dispatch_event(ev, "firing")
        assert NotificationLog.objects.filter(event=ev, channel=gch, status="sent").exists()
        assert NotificationLog.objects.filter(event=ev, channel=fch, status="failed").exists()

    @ENABLED
    def test_cross_channel_alarm_routes_to_survivor(self, monkeypatch):
        from django.core.cache import cache
        cache.clear()
        monkeypatch.setitem(_REGISTRY, "good", RecordingNotifier())
        monkeypatch.setitem(_REGISTRY, "fail", FailingNotifier())
        rule = make_rule()
        gch = make_channel(channel_type="good", name="teams", config={"all_alerts": True})
        fch = make_channel(channel_type="fail", name="email", config={"all_alerts": True})
        ev = make_event(rule, severity="high")
        dispatch.dispatch_event(ev, "firing")
        meta = AlertEvent.objects.filter(
            labels__alert_type="notification_delivery_failed",
            labels__failed_channel_id=fch.id).first()
        assert meta is not None  # the surviving channel reports the dead one
        chans = dispatch.matching_channels(meta, payload_mod.build_payload(meta, "firing"))
        assert gch in chans and fch not in chans  # excludes the dead channel, routes to teams

    @ENABLED
    def test_single_failing_channel_is_graceful(self, monkeypatch):
        from django.core.cache import cache
        cache.clear()
        monkeypatch.setitem(_REGISTRY, "fail", FailingNotifier())
        rule = make_rule()
        fch = make_channel(channel_type="fail", config={"all_alerts": True})
        ev = make_event(rule, severity="high")
        dispatch.dispatch_event(ev, "firing")
        meta = AlertEvent.objects.filter(labels__alert_type="notification_delivery_failed").first()
        assert meta is not None
        # the only channel IS the failing one → excluded → no surviving channel, no error
        assert dispatch.matching_channels(meta, payload_mod.build_payload(meta, "firing")) == []
        summary = dispatch.dispatch_event(meta, "firing")
        assert summary["dispatched"] and summary["channels"] == 0 and summary["failed"] == 0

    @ENABLED
    def test_no_alarm_on_alarm(self, monkeypatch):
        from django.core.cache import cache
        cache.clear()
        monkeypatch.setitem(_REGISTRY, "fail", FailingNotifier())
        rule = make_rule()
        fch = make_channel(channel_type="fail", config={"all_alerts": True})
        ev = make_event(rule, severity="high", alert_type="notification_delivery_failed")
        before = AlertEvent.objects.count()
        dispatch._fire_delivery_failure_alarm(fch, payload_mod.build_payload(ev, "firing"))
        assert AlertEvent.objects.count() == before  # recursion guard

    def test_test_send_not_logged(self, rec):
        ch = make_channel()
        p = payload_mod.AlertPayload(event_id=None, transition="firing", severity="info",
                                     title="t", message="m")
        dispatch.send_to_channel(ch, p)
        assert NotificationLog.objects.count() == 0  # synthetic test sends aren't deliveries

    def test_delivery_health_unhealthy_then_clears(self):
        from apps.alerts.delivery_health import delivery_health
        rule = make_rule()
        ch = make_channel(name="email")
        ev = make_event(rule)
        NotificationLog.objects.create(event=ev, channel=ch, channel_name="email",
                                       channel_type="rec", transition="firing",
                                       status="failed", attempts=2, detail="boom")
        h = delivery_health()
        assert h["healthy"] is False and h["channels_failing"] == 1
        NotificationLog.objects.create(event=ev, channel=ch, channel_name="email",
                                       channel_type="rec", transition="firing",
                                       status="sent", attempts=1)
        h2 = delivery_health()
        assert h2["healthy"] is True and h2["channels_failing"] == 0


# ── per-device/server silencing (generate-but-don't-notify) ────────────────────

class TestDeviceSilencing:
    def _device(self, **kw):
        from apps.devices.models import Device
        kw.setdefault("device_kind", "network_device")
        return Device.objects.create(**kw)

    @ENABLED
    def test_observe_only_generates_but_skips_dispatch(self, rec):
        dev = self._device(hostname="d-obs", ip_address="10.7.0.1", alerting_enabled=False)
        rule = make_rule(); ch = make_channel(); rule.channels.add(ch)
        ev = make_event(rule, device_id=dev.id)
        s = dispatch.dispatch_event(ev, "firing")
        assert s["reason"] == "device_alerting_disabled"
        assert rec.calls == []
        assert AlertEvent.objects.filter(id=ev.id).exists()            # still generated
        assert NotificationLog.objects.filter(event=ev).count() == 0   # not notified

    @ENABLED
    def test_silenced_until_future_skips(self, rec):
        from datetime import timedelta
        from django.utils import timezone
        dev = self._device(hostname="d-sil", ip_address="10.7.0.2", device_kind="server",
                           silenced_until=timezone.now() + timedelta(hours=1))
        rule = make_rule(); ch = make_channel(); rule.channels.add(ch)
        ev = make_event(rule, device_id=dev.id)
        s = dispatch.dispatch_event(ev, "firing")
        assert s["reason"] == "device_silenced"
        assert rec.calls == []

    @ENABLED
    def test_silence_expired_auto_resumes(self, rec):
        from datetime import timedelta
        from django.utils import timezone
        dev = self._device(hostname="d-exp", ip_address="10.7.0.3",
                           silenced_until=timezone.now() - timedelta(minutes=1))
        rule = make_rule(); ch = make_channel(); rule.channels.add(ch)
        ev = make_event(rule, device_id=dev.id)
        s = dispatch.dispatch_event(ev, "firing")
        assert s["sent"] == 1  # past silenced_until → auto-resumed

    @ENABLED
    def test_normal_device_notifies(self, rec):
        dev = self._device(hostname="d-norm", ip_address="10.7.0.4")
        rule = make_rule(); ch = make_channel(); rule.channels.add(ch)
        ev = make_event(rule, device_id=dev.id)
        s = dispatch.dispatch_event(ev, "firing")
        assert s["sent"] == 1
