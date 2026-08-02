"""Collector-originated ping/RTT for agent HOSTS (Stage 1).

The reachability monitor probes agent hosts against the agent's REAL IP
(Agent.last_ip), stores RTT in the same device_reachability schema (so the
Servers UI reads it by device_id), and fires/resolves a distinct **Host
Unreachable** alert — never a false "unreachable" for a host with no routable IP
(the #133 lesson).
"""
import pytest

pytestmark = pytest.mark.django_db


def _cmd():
    from apps.devices.management.commands.run_reachability_monitor import Command
    c = Command()
    c._agent_fails = {}
    return c


def _default_collector():
    from apps.collectors.models import Collector
    return Collector.objects.create(
        name="local", api_key_hash="k-local", is_default=True,
        collector_type=Collector.CollectorType.LOCAL)


def _agent(hostname, last_ip, *, status=None, collector=None, site=None):
    from apps.agents.models import Agent
    from apps.devices.models import Device
    dev = Device.objects.create(hostname=hostname, ip_address=f"fd00::{abs(hash(hostname)) % 9999}",
                                site=site, collector=collector)
    a = Agent.objects.create(hostname=hostname, device=dev, last_ip=last_ip,
                             status=status or Agent.Status.ACTIVE)
    return a


# ── is_pingable_ip ────────────────────────────────────────────────────────────

class TestIsPingableIp:
    def test_real_ips_pass(self):
        from apps.devices.management.commands.run_reachability_monitor import is_pingable_ip
        assert is_pingable_ip("8.8.8.8")
        assert is_pingable_ip("192.168.98.50")   # private LAN IPv4 is on-site pingable
        assert is_pingable_ip("10.0.0.5")

    def test_non_routable_rejected(self):
        from apps.devices.management.commands.run_reachability_monitor import is_pingable_ip
        assert not is_pingable_ip(None)
        assert not is_pingable_ip("127.0.0.1")            # loopback
        assert not is_pingable_ip("::1")
        assert not is_pingable_ip("0.0.0.0")              # unspecified
        assert not is_pingable_ip("169.254.1.1")          # link-local
        assert not is_pingable_ip("fd00:dead:beef::1")    # ULA placeholder space
        assert not is_pingable_ip("not-an-ip")


# ── target-set resolution ─────────────────────────────────────────────────────

class TestFetchAgentTargets:
    def test_includes_agent_with_routable_ip_under_default_collector(self):
        _default_collector()
        _agent("srv-1", "192.168.98.50")
        targets = _cmd()._fetch_agent_targets()
        hosts = {t["hostname"] for t in targets}
        assert "srv-1" in hosts
        t = next(t for t in targets if t["hostname"] == "srv-1")
        assert t["ip_address"] == "192.168.98.50" and t["_is_agent"] is True

    def test_excludes_agent_with_no_or_loopback_ip_no_false_unreachable(self):
        _default_collector()
        _agent("noip", None)
        _agent("loop", "127.0.0.1")
        hosts = {t["hostname"] for t in _cmd()._fetch_agent_targets()}
        assert "noip" not in hosts and "loop" not in hosts

    def test_excludes_revoked_agent(self):
        from apps.agents.models import Agent
        _default_collector()
        _agent("dead", "10.0.0.9", status=Agent.Status.REVOKED)
        assert "dead" not in {t["hostname"] for t in _cmd()._fetch_agent_targets()}

    def test_excludes_agent_owned_by_remote_collector(self):
        from apps.collectors.models import Collector
        from apps.devices.models import Site
        _default_collector()
        remote = Collector.objects.create(name="edge", api_key_hash="k-edge",
                                          collector_type=Collector.CollectorType.REMOTE)
        site = Site.objects.create(name="Branch", default_collector=remote)
        _agent("remote-srv", "10.1.1.1", site=site)
        assert "remote-srv" not in {t["hostname"] for t in _cmd()._fetch_agent_targets()}


# ── host-unreachable alert ────────────────────────────────────────────────────

class TestApplyAgents:
    def _target(self, agent):
        return {"id": agent.device_id, "hostname": agent.hostname,
                "ip_address": agent.last_ip, "management_ip": agent.last_ip,
                "_agent_id": str(agent.id), "_is_agent": True}

    def test_fires_after_threshold_then_resolves(self):
        from apps.alerts.models import AlertEvent
        from apps.devices.management.commands.run_reachability_monitor import FAILURE_THRESHOLD
        a = _agent("flaky", "192.168.98.51")
        cmd = _cmd()
        t = self._target(a)
        # below threshold → no alert
        for _ in range(FAILURE_THRESHOLD - 1):
            cmd._apply_agents([(t, False, "tcp", None)])
        assert not AlertEvent.objects.filter(labels__alert_type="host_unreachable").exists()
        # threshold reached → fires once, with device_id + agent_id linkage
        cmd._apply_agents([(t, False, "tcp", None)])
        ev = AlertEvent.objects.get(labels__alert_type="host_unreachable", state=AlertEvent.State.FIRING)
        assert ev.labels["device_id"] == a.device_id
        assert ev.labels["agent_id"] == str(a.id)
        # still down → debounced (no second event)
        cmd._apply_agents([(t, False, "tcp", None)])
        assert AlertEvent.objects.filter(labels__alert_type="host_unreachable",
                                         state=AlertEvent.State.FIRING).count() == 1
        # reachable again → resolves
        cmd._apply_agents([(t, True, "tcp/22", 1.2)])
        ev.refresh_from_db()
        assert ev.state == AlertEvent.State.RESOLVED and ev.resolved_at is not None

    def test_no_alert_while_reachable(self):
        from apps.alerts.models import AlertEvent
        a = _agent("healthy", "10.0.0.20")
        _cmd()._apply_agents([(self._target(a), True, "tcp/22", 0.8)])
        assert not AlertEvent.objects.filter(labels__alert_type="host_unreachable").exists()


# ── server-detail Network chip ────────────────────────────────────────────────

class TestNetworkState:
    def test_not_probed_when_no_routable_ip(self):
        from apps.agents.server_views import ServerViewSet
        a = _agent("noip2", None)
        st = ServerViewSet._network_state(a)
        assert st["probed"] is False and st["reachable"] is None

    def test_reachable_when_no_open_alert(self):
        from apps.agents.server_views import ServerViewSet
        a = _agent("ok2", "192.168.98.52")
        st = ServerViewSet._network_state(a)
        assert st["probed"] is True and st["reachable"] is True

    def test_unreachable_when_alert_firing(self):
        from apps.agents.server_views import ServerViewSet
        from apps.alerts.models import AlertEvent, AlertRule
        a = _agent("down2", "192.168.98.53")
        rule = AlertRule.objects.create(name="Host Unreachable",
                                        severity=AlertRule.Severity.HIGH, condition={})
        AlertEvent.objects.create(rule=rule, state=AlertEvent.State.FIRING,
                                  labels={"alert_type": "host_unreachable", "agent_id": str(a.id)})
        st = ServerViewSet._network_state(a)
        assert st["probed"] is True and st["reachable"] is False
