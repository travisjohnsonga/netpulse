"""
Template-based configuration compliance engine.

Renders a Jinja2 template (the *expected* config for a device's role / platform /
site) and diffs it against the device's latest running config, classifying each
deviation as:

  MISSING — a line in the template that is not in the device config
  DRIFT   — a line present but with a different value (same command prefix)
  EXTRA   — a line in the device config not in the template (reserved; the
            baseline-template diff below reports MISSING/DRIFT only)

Results are stored as ComplianceTemplateResult rows. The diff is intentionally
line-oriented and order-insensitive (network configs are sets of statements).
"""
from __future__ import annotations

import logging

from jinja2 import BaseLoader, Environment

from .models import (
    ComplianceTemplate,
    ComplianceTemplateResult,
    DeviceComplianceOverride,
)

logger = logging.getLogger(__name__)


class ComplianceEngine:

    def render_template(self, template, device, overrides=None) -> str:
        """Render a template's Jinja2 content for a device.

        Variable precedence: template defaults < device overrides. A read-only
        ``device`` context (hostname/ip/platform/site/role) is always injected.
        """
        variables = dict(template.variables or {})
        if overrides:
            variables.update(overrides)

        variables["device"] = {
            "hostname": device.hostname,
            "ip": str(device.management_ip or device.ip_address or ""),
            "platform": device.platform,
            "site": device.site.name if device.site else "",
            "role": device.role.name if device.role else "",
        }

        env = Environment(loader=BaseLoader(), keep_trailing_newline=False)
        return env.from_string(template.template_content).render(**variables)

    def check_device(self, device, template, config_text=None) -> ComplianceTemplateResult:
        """
        Check a device's config against a template. Returns an UNSAVED
        ComplianceTemplateResult (caller sets config_snapshot and saves).
        """
        if config_text is None:
            from apps.configbackup.models import DeviceConfig
            backup = (
                DeviceConfig.objects.filter(device=device)
                .order_by("-collected_at")
                .first()
            )
            if not backup:
                return self._error_result(device, template, "No config backup available")
            config_text = backup.content

        # Device-specific variable overrides, if any.
        try:
            overrides = DeviceComplianceOverride.objects.get(
                device=device, template=template).variables
        except DeviceComplianceOverride.DoesNotExist:
            overrides = {}

        try:
            expected = self.render_template(template, device, overrides)
        except Exception as exc:  # noqa: BLE001 — template authoring error
            return self._error_result(device, template, f"Template render error: {exc}")

        findings = self.compare_configs(expected, config_text)

        total = len(expected.splitlines())
        issues = len(findings)
        score = max(0.0, 100.0 * ((total - issues) / total)) if total > 0 else 100.0

        # OS-version compliance component (device-level): prepend any OS findings
        # and fold the penalty into this device's score, clamped to [0, 100].
        from .os_policy import os_compliance_findings
        os_delta, os_findings = os_compliance_findings(device)
        if os_findings:
            findings = os_findings + findings
            score = max(0.0, min(100.0, score + os_delta))

        return ComplianceTemplateResult(
            device=device,
            template=template,
            status=ComplianceTemplateResult.Status.COMPLIANT if score == 100
            else ComplianceTemplateResult.Status.NON_COMPLIANT,
            score=round(score, 1),
            findings=findings,
            missing_count=sum(1 for f in findings if f["type"] == "MISSING"),
            extra_count=sum(1 for f in findings if f["type"] == "EXTRA"),
            drift_count=sum(1 for f in findings if f["type"] == "DRIFT"),
            remediation=self.generate_remediation(findings),
        )

    def compare_configs(self, expected: str, actual: str) -> list[dict]:
        """Diff expected vs actual config → list of MISSING/DRIFT findings.

        Blank lines and comment lines (starting ``!``) are ignored on both sides.
        """
        expected_lines = self._meaningful_lines(expected)
        actual_lines = self._meaningful_lines(actual)
        actual_set = set(actual_lines)

        findings: list[dict] = []
        for line in expected_lines:
            if line in actual_set:
                continue  # compliant

            drift = self._find_drift(line, actual_lines)
            if drift:
                findings.append({
                    "type": "DRIFT", "severity": "medium",
                    "expected": line, "actual": drift, "line": line,
                })
            else:
                findings.append({
                    "type": "MISSING", "severity": "high",
                    "expected": line, "actual": None, "line": line,
                })
        return findings

    @staticmethod
    def _meaningful_lines(text: str) -> list[str]:
        return [
            ln.rstrip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("!")
        ]

    def _find_drift(self, expected_line: str, actual_lines: list[str]):
        """A line sharing the command prefix (first 2 words) but a different value
        (e.g. ``ntp server 1.1.1.1`` vs ``ntp server 2.2.2.2``). None if no match."""
        parts = expected_line.split()
        if len(parts) < 2:
            return None
        prefix = " ".join(parts[:2])
        for actual in actual_lines:
            if actual.startswith(prefix) and actual != expected_line:
                return actual
        return None

    def generate_remediation(self, findings: list[dict]) -> str:
        """Config commands to bring the device into compliance."""
        lines: list[str] = []
        for f in findings:
            if f["type"] == "MISSING":
                lines.append(f["expected"])
            elif f["type"] == "DRIFT":
                lines.append(f"no {f['actual']}")
                lines.append(f["expected"])
            elif f["type"] == "EXTRA":
                lines.append(f"no {f['line']}")
        return "\n".join(lines)

    @staticmethod
    def _error_result(device, template, message: str) -> ComplianceTemplateResult:
        return ComplianceTemplateResult(
            device=device,
            template=template,
            status=ComplianceTemplateResult.Status.ERROR,
            score=None,
            findings=[{
                "type": "ERROR", "severity": "high",
                "line": message, "expected": None, "actual": None, "context": message,
            }],
            remediation="",
        )


# ── Template selection + orchestration ──────────────────────────────────────────

def get_templates_for_device(device) -> list[ComplianceTemplate]:
    """
    Enabled templates that apply to a device. A template applies when every
    constraint it sets matches the device (a blank constraint is a wildcard):
      role set   → device.role must match
      platform   → device.platform must match
      site set   → device.site must match
    Ordered by specificity (role > platform > site > global) for display.
    """
    applicable = []
    for tmpl in ComplianceTemplate.objects.filter(enabled=True).select_related("role", "site"):
        if tmpl.role_id and tmpl.role_id != device.role_id:
            continue
        if tmpl.platform and tmpl.platform != device.platform:
            continue
        if tmpl.site_id and tmpl.site_id != device.site_id:
            continue
        applicable.append(tmpl)

    def specificity(t):
        return (0 if t.role_id else 1, 0 if t.platform else 1, 0 if t.site_id else 1, t.name)

    return sorted(applicable, key=specificity)


def run_compliance_for_device(device, config_snapshot=None, *, store_score=True,
                              role_cache=None) -> list[ComplianceTemplateResult]:
    """
    Run every applicable template against a device, saving a result per template.
    Best-effort: each template is independent; a failure becomes an error result.
    Returns the saved results.

    By default the device's combined weighted score is also persisted to
    DeviceComplianceScore afterwards, so the device list always reflects a fresh
    run regardless of entry point. Callers that do further reconciliation before
    scoring (e.g. config collection updates startup-match, then stores) pass
    ``store_score=False`` and call ``run_and_store_compliance`` themselves.
    """
    engine = ComplianceEngine()
    config_text = config_snapshot.content if config_snapshot is not None else None
    results = []
    for tmpl in get_templates_for_device(device):
        try:
            result = engine.check_device(device, tmpl, config_text=config_text)
        except Exception as exc:  # noqa: BLE001 — never let one template break the run
            logger.warning("compliance check failed for %s / %s: %s", device.hostname, tmpl.name, exc)
            result = ComplianceEngine._error_result(device, tmpl, str(exc))
        result.config_snapshot = config_snapshot
        result.save()
        results.append(result)

    if store_score:
        # Always persist the combined weighted score so the device list and the
        # Compliance tab agree. Best-effort — never let scoring break the run.
        try:
            from .device_score import run_and_store_compliance
            run_and_store_compliance(device, role_cache=role_cache)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compliance score store failed for %s: %s", device.hostname, exc)

    return results
