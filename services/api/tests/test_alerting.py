import pytest

from apps.alerting.engine import (
    find_matching_route, get_on_call_user, route_matches, step_email_recipients,
)
from apps.alerting.models import (
    AlertRoute, EscalationPolicy, EscalationStep, OnCallSchedule, OnCallShift,
    Team, TeamMember,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def team():
    return Team.objects.create(name="Network Ops", color="#ef4444")


@pytest.fixture
def policy(team):
    return EscalationPolicy.objects.create(name="Network Critical", team=team)


# ── Route matching (pure-ish) ────────────────────────────────────────────────

class TestRouteMatching:
    def test_empty_conditions_match_all(self, policy):
        r = AlertRoute.objects.create(name="catch-all", escalation_policy=policy)
        assert route_matches(r, severity="low", source="snmp", check_type=None) is True

    def test_severity_and_source_and_logic(self, policy):
        r = AlertRoute.objects.create(name="crit-checks", escalation_policy=policy,
                                      match_severity=["critical", "high"], match_source=["check_engine"])
        assert route_matches(r, severity="high", source="check_engine") is True
        assert route_matches(r, severity="low", source="check_engine") is False   # severity fails
        assert route_matches(r, severity="high", source="snmp") is False          # source fails

    def test_check_type_match(self, policy):
        r = AlertRoute.objects.create(name="tls", escalation_policy=policy, match_check_types=["tls"])
        assert route_matches(r, check_type="tls") is True
        assert route_matches(r, check_type="http") is False

    def test_site_match(self, policy):
        from apps.devices.models import Site
        s = Site.objects.create(name="DC-1")
        r = AlertRoute.objects.create(name="dc1", escalation_policy=policy)
        r.match_sites.add(s)
        assert route_matches(r, site_id=s.id) is True
        assert route_matches(r, site_id=999) is False

    def test_find_matching_route_respects_priority(self, policy):
        AlertRoute.objects.create(name="catch-all", escalation_policy=policy, priority=100)
        AlertRoute.objects.create(name="high-pri", escalation_policy=policy, priority=1,
                                  match_severity=["critical"])
        assert find_matching_route(severity="critical").name == "high-pri"
        assert find_matching_route(severity="low").name == "catch-all"

    def test_inactive_route_skipped(self, policy):
        AlertRoute.objects.create(name="off", escalation_policy=policy, priority=1, is_active=False)
        catch = AlertRoute.objects.create(name="catch-all", escalation_policy=policy, priority=100)
        assert find_matching_route(severity="high").id == catch.id


# ── Recipients + email ───────────────────────────────────────────────────────

class TestNotification:
    def test_step_recipients_team_uses_on_call_lead(self, team, policy, django_user_model):
        # No active shift → on-call falls back to the team lead (single recipient).
        lead = django_user_model.objects.create_user(username="a", password="x", email="a@co", role="engineer")
        member = django_user_model.objects.create_user(username="b", password="x", email="b@co", role="engineer")
        TeamMember.objects.create(team=team, user=member, role="member", notify_email=True)
        TeamMember.objects.create(team=team, user=lead, role="lead", notify_email=True)
        step = EscalationStep.objects.create(policy=policy, step_number=1, notify_team=team)
        assert step_email_recipients(step) == [(lead, "a@co")]

    def test_step_recipients_explicit_user(self, policy, django_user_model):
        u = django_user_model.objects.create_user(username="c", password="x", email="c@co", role="engineer")
        step = EscalationStep.objects.create(policy=policy, step_number=1, notify_user=u)
        assert step_email_recipients(step) == [(u, "c@co")]

    def test_process_alert_event_sends_email(self, team, policy, django_user_model, mailoutbox):
        from apps.alerting.engine import process_alert_event
        from apps.alerting.models import AlertNotification
        from apps.alerts.models import AlertRule, AlertEvent

        u = django_user_model.objects.create_user(username="d", password="x", email="d@co", role="engineer")
        TeamMember.objects.create(team=team, user=u, notify_email=True)
        EscalationStep.objects.create(policy=policy, step_number=1, notify_team=team)
        AlertRoute.objects.create(name="all", escalation_policy=policy, match_severity=["high"])

        rule = AlertRule.objects.create(name="Svc Down", severity="high", condition={})
        ev = AlertEvent.objects.create(rule=rule, state="firing",
                                       annotations={"severity": "high", "title": "Service Down: API"})
        result = process_alert_event(ev)
        assert result["matched"] is True and result["notified"] == 1
        assert len(mailoutbox) == 1
        assert "Service Down: API" in mailoutbox[0].subject
        assert AlertNotification.objects.filter(alert_event=ev, status="sent").count() == 1

    def test_process_alert_event_no_route(self, django_user_model):
        from apps.alerting.engine import process_alert_event
        from apps.alerts.models import AlertRule, AlertEvent
        rule = AlertRule.objects.create(name="x", severity="low", condition={})
        ev = AlertEvent.objects.create(rule=rule, state="firing", annotations={"severity": "low"})
        assert process_alert_event(ev) == {"matched": False, "route": None, "notified": 0}


# ── API ──────────────────────────────────────────────────────────────────────

class TestAlertingApi:
    def test_team_crud_and_members(self, auth_client, django_user_model):
        resp = auth_client.post("/api/alerting/teams/", {"name": "NetOps", "color": "#ef4444"}, format="json")
        assert resp.status_code == 201, resp.content
        tid = resp.json()["id"]
        u = django_user_model.objects.create_user(username="m1", password="x", email="m1@co", role="engineer")
        add = auth_client.post(f"/api/alerting/teams/{tid}/members/", {"user": u.id, "role": "lead"}, format="json")
        assert add.status_code == 201
        members = auth_client.get(f"/api/alerting/teams/{tid}/members/").json()
        assert len(members) == 1 and members[0]["role"] == "lead"
        rm = auth_client.delete(f"/api/alerting/teams/{tid}/members/{u.id}/")
        assert rm.status_code == 204
        assert auth_client.get(f"/api/alerting/teams/{tid}/members/").json() == []

    def test_policy_with_steps(self, auth_client, team):
        p = auth_client.post("/api/alerting/policies/", {"name": "P1", "team": team.id}, format="json").json()
        auth_client.post(f"/api/alerting/policies/{p['id']}/steps/",
                         {"step_number": 1, "delay_minutes": 0, "notify_team": team.id}, format="json")
        detail = auth_client.get(f"/api/alerting/policies/{p['id']}/").json()
        assert len(detail["steps"]) == 1 and detail["steps"][0]["step_number"] == 1

    def test_route_test_action(self, auth_client, policy):
        auth_client.post("/api/alerting/routes/", {
            "name": "crit", "escalation_policy": policy.id, "priority": 1,
            "match_severity": ["critical"],
        }, format="json")
        hit = auth_client.post("/api/alerting/routes/test/", {"severity": "critical"}, format="json").json()
        assert hit["matched"] is True and hit["route"]["name"] == "crit"
        miss = auth_client.post("/api/alerting/routes/test/", {"severity": "low"}, format="json").json()
        assert miss["matched"] is False and miss["route"] is None


# ── Stage 2: on-call resolution ──────────────────────────────────────────────

class TestOnCall:
    def test_active_shift_user(self, team, django_user_model):
        from django.utils import timezone
        from datetime import timedelta
        u = django_user_model.objects.create_user(username="oncall", password="x", email="o@co", role="engineer")
        sched = OnCallSchedule.objects.create(team=team, name="Primary")
        now = timezone.now()
        OnCallShift.objects.create(schedule=sched, user=u,
                                   start_datetime=now - timedelta(hours=1), end_datetime=now + timedelta(hours=1))
        assert get_on_call_user(team) == u

    def test_falls_back_to_lead_then_member(self, team, django_user_model):
        lead = django_user_model.objects.create_user(username="lead", password="x", email="l@co", role="engineer")
        member = django_user_model.objects.create_user(username="mem", password="x", email="m@co", role="engineer")
        TeamMember.objects.create(team=team, user=member, role="member")
        TeamMember.objects.create(team=team, user=lead, role="lead")
        assert get_on_call_user(team) == lead  # no active shift → lead

    def test_none_when_empty(self, team):
        assert get_on_call_user(team) is None

    def test_step_recipients_prefer_on_call(self, team, policy, django_user_model):
        from django.utils import timezone
        from datetime import timedelta
        oncall = django_user_model.objects.create_user(username="oc", password="x", email="oc@co", role="engineer")
        other = django_user_model.objects.create_user(username="ot", password="x", email="ot@co", role="engineer")
        TeamMember.objects.create(team=team, user=other, notify_email=True)
        sched = OnCallSchedule.objects.create(team=team)
        now = timezone.now()
        OnCallShift.objects.create(schedule=sched, user=oncall,
                                   start_datetime=now - timedelta(hours=1), end_datetime=now + timedelta(hours=1))
        step = EscalationStep.objects.create(policy=policy, step_number=1, notify_team=team)
        assert step_email_recipients(step) == [(oncall, "oc@co")]


class TestSlackChannel:
    def test_send_slack_posts(self, monkeypatch):
        from apps.alerting import channels
        calls = {}
        class Resp: status_code = 200
        def fake_post(url, json=None, timeout=None):
            calls["url"] = url; calls["json"] = json; return Resp()
        import sys, types
        fake_requests = types.ModuleType("requests"); fake_requests.post = fake_post
        monkeypatch.setitem(sys.modules, "requests", fake_requests)
        ok, err = channels.send_slack("https://hooks.slack/x", "hello")
        assert ok and calls["json"] == {"text": "hello"}

    def test_send_slack_no_url(self):
        from apps.alerting import channels
        ok, err = channels.send_slack("", "x")
        assert ok is False


# ── Stage 2: acknowledge / snooze ────────────────────────────────────────────

class TestAcknowledge:
    def _event(self):
        from apps.alerts.models import AlertRule, AlertEvent
        rule = AlertRule.objects.create(name="r", severity="high", condition={})
        return AlertEvent.objects.create(rule=rule, state="firing")

    def test_acknowledge_records_and_cancels_pending(self, auth_client, user):
        from apps.alerting.models import AlertNotification
        ev = self._event()
        AlertNotification.objects.create(alert_event=ev, channel="email", status="pending")
        resp = auth_client.post(f"/api/alerts/events/{ev.id}/acknowledge/",
                                {"note": "on it", "snooze_minutes": 30}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["note"] == "on it" and body["snoozed_until"] is not None
        assert AlertNotification.objects.get(alert_event=ev).status == "cancelled"

    def test_snooze(self, auth_client, user):
        ev = self._event()
        resp = auth_client.post(f"/api/alerts/events/{ev.id}/snooze/", {"minutes": 15}, format="json")
        assert resp.status_code == 200 and resp.json()["snoozed_until"] is not None


class TestOnCallApi:
    def test_schedule_with_shift_and_current(self, auth_client, team, django_user_model):
        from django.utils import timezone
        from datetime import timedelta
        u = django_user_model.objects.create_user(username="s1", password="x", email="s1@co", role="engineer")
        sched = auth_client.post("/api/alerting/schedules/", {"team": team.id, "name": "Primary"}, format="json").json()
        now = timezone.now()
        auth_client.post(f"/api/alerting/schedules/{sched['id']}/shifts/", {
            "user": u.id, "start_datetime": (now - timedelta(hours=1)).isoformat(),
            "end_datetime": (now + timedelta(hours=1)).isoformat(),
        }, format="json")
        oncall = auth_client.get("/api/alerting/on-call/").json()
        row = next(r for r in oncall if r["team"] == team.id)
        assert row["username"] == "s1"
