"""run_scheduler — the authoritative periodic-task scheduler."""
import pytest

pytestmark = pytest.mark.django_db


def _run_once(monkeypatch):
    """Run one scheduler pass with call_command/purge mocked; return command names."""
    from apps.core.management.commands import run_scheduler as mod
    calls = []
    monkeypatch.setattr(mod, "call_command", lambda *a, **k: calls.append(a[0] if a else None))
    import apps.alerts.management.commands.purge_resolved_alerts as purge
    monkeypatch.setattr(purge, "purge_resolved_alerts", lambda days: 0)
    mod.Command().handle(interval=86400, tick=300, once=True)
    return calls


class TestScheduler:
    def test_startup_seeds_and_unseals(self, monkeypatch):
        calls = _run_once(monkeypatch)
        # Startup one-shots run.
        assert "seed_alert_rules" in calls
        assert "init_openbao" in calls

    def test_arp_mac_and_oui_not_run_on_first_pass(self, monkeypatch):
        # 6h / weekly tasks fire one interval after startup, not immediately.
        calls = _run_once(monkeypatch)
        assert "collect_arp_mac" not in calls

    def test_seeds_oui_when_table_empty(self, monkeypatch):
        calls = _run_once(monkeypatch)
        assert "update_mac_vendors" in calls  # MACVendor table empty in test DB

    def test_skips_oui_seed_when_table_populated(self, monkeypatch):
        from apps.arp_mac.models import MACVendor
        MACVendor.objects.create(oui="aa:bb:cc", vendor="Acme")
        calls = _run_once(monkeypatch)
        assert "update_mac_vendors" not in calls  # already populated → no startup reload
