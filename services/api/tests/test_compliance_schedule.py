"""Daily scheduled compliance run — apps.compliance.scheduler.run_due_compliance.

Hour-gated at COMPLIANCE_RUN_HOUR (default 03:00) + same-day deduped via a
SystemSetting; the fleet pass itself is exercised in test_compliance_run.
"""
import datetime

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.compliance import runner, scheduler
from apps.core.models import SystemSetting

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear():
    cache.clear()
    SystemSetting.objects.filter(key=scheduler._LAST_RUN_KEY).delete()
    yield
    cache.clear()


def _at(hour):
    # A fixed aware datetime at the given hour.
    return timezone.make_aware(datetime.datetime(2026, 6, 21, hour, 5, 0))


@pytest.fixture
def _capture(monkeypatch):
    """Replace the blocking run so tests don't touch the device fleet."""
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return True
    monkeypatch.setattr(runner, "run_all_blocking", _fake)
    return calls


def test_skips_outside_target_hour(_capture):
    assert scheduler.run_due_compliance(now=_at(2)) is False
    assert scheduler.run_due_compliance(now=_at(4)) is False
    assert _capture["n"] == 0


def test_runs_at_target_hour(_capture):
    assert scheduler.run_due_compliance(now=_at(3)) is True
    assert _capture["n"] == 1
    # The day is marked done.
    assert SystemSetting.get(scheduler._LAST_RUN_KEY) == _at(3).date().isoformat()


def test_same_day_dedup(_capture):
    assert scheduler.run_due_compliance(now=_at(3)) is True
    # A second tick within the hour must NOT re-run.
    assert scheduler.run_due_compliance(now=_at(3)) is False
    assert _capture["n"] == 1


def test_runs_again_next_day(_capture):
    scheduler.run_due_compliance(now=_at(3))
    next_day = timezone.make_aware(datetime.datetime(2026, 6, 22, 3, 5, 0))
    assert scheduler.run_due_compliance(now=next_day) is True
    assert _capture["n"] == 2


def test_configurable_hour(monkeypatch, _capture):
    monkeypatch.setattr(scheduler, "_RUN_HOUR", 5)
    assert scheduler.run_due_compliance(now=_at(3)) is False
    assert scheduler.run_due_compliance(now=_at(5)) is True
    assert _capture["n"] == 1


def test_marks_day_even_if_run_already_active(monkeypatch):
    # run_all_blocking returns False when another run holds the lock.
    monkeypatch.setattr(runner, "run_all_blocking", lambda *a, **k: False)
    assert scheduler.run_due_compliance(now=_at(3)) is False
    # Still deduped for the day (won't hammer a run that's already going).
    assert SystemSetting.get(scheduler._LAST_RUN_KEY) == _at(3).date().isoformat()
