"""Unit tests for the health-check runner (rendering + aggregation + exit), with
checks mocked — these do NOT touch real infrastructure."""
import json

import pytest

from apps.core.management.commands.run_health_checks import (
    HealthCheckRunner, CheckResult, PASS, WARN, FAIL, ok, warn, fail,
)


class TestCheckResult:
    def test_warn_does_not_fail_suite(self):
        assert warn("C", "w").passed is True
        assert ok("C", "o").passed is True
        assert fail("C", "f").passed is False


class TestRunner:
    def _runner_with(self, results, **kw):
        r = HealthCheckRunner(**kw)
        # Replace the registry with a single synthetic check returning `results`.
        r.CHECKS = [("Synthetic", "_synthetic")]
        r._synthetic = lambda: results  # type: ignore[attr-defined]
        return r

    def test_all_pass_collects_results(self):
        r = self._runner_with([ok("DB", "conn"), ok("DB", "rw")])
        res = r.run_all()
        assert len(res) == 2 and all(x.passed for x in res)

    def test_failure_marks_not_passed(self):
        r = self._runner_with([ok("DB", "conn"), fail("DB", "rw", "42", "0")])
        res = r.run_all()
        assert not all(x.passed for x in res)

    def test_fail_fast_stops_at_first_failure(self):
        r = self._runner_with([fail("DB", "conn"), ok("DB", "rw")], fail_fast=True)
        res = r.run_all()
        assert len(res) == 1 and res[0].status == FAIL

    def test_check_exception_becomes_failure(self):
        r = HealthCheckRunner()

        def _boom():
            raise RuntimeError("nope")
        r.CHECKS = [("X", "_boom")]
        r._boom = _boom  # type: ignore[attr-defined]
        res = r.run_all()
        assert len(res) == 1 and res[0].status == FAIL and "nope" in res[0].actual

    def test_json_output_is_valid_and_summarizes(self):
        r = self._runner_with([ok("DB", "a"), warn("DB", "b"), fail("DB", "c")], json_output=True)
        res = r.run_all()
        doc = json.loads(r.render(res))
        assert doc["summary"] == {"total": 3, "passed": 1, "warnings": 1, "failed": 1, "ok": False}
        assert len(doc["results"]) == 3

    def test_console_report_shows_counts_and_fix(self):
        r = self._runner_with([ok("DB", "a"), fail("DB", "c", "x", "y", "do thing")])
        text = r.render(r.run_all())
        assert "PASSED: 1/2" in text and "FAILED: 1" in text
        assert "Fix: do thing" in text

    def test_passed_property_aggregation(self):
        all_ok = [ok("C", "a"), warn("C", "b")]
        assert all(x.passed for x in all_ok)        # warnings don't fail
        assert not all(x.passed for x in all_ok + [fail("C", "c")])
