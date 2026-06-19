"""
Per-device weighted compliance score.

The device Compliance tab combines three independent signals into one score:

    Template compliance   50%   config matches its Jinja2 role template
    Interface rules       30%   LLDP-aware per-port checks pass
    Role consistency      20%   device has the same VLANs/settings as its peers

Components that don't apply to a device (no template, no matching interface
rules, not in a checked role) are dropped and the remaining weights are
renormalised, so a switch with only interface rules is still scored fairly.

This module also surfaces the per-finding detail the UI renders: the failing
interface's config block and a platform-specific suggested fix.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── suggested fixes ──────────────────────────────────────────────────────────
# Keyed by platform → substring of the failing check value → remediation template.
# {port}/{voice_vlan} are filled when known; unknown placeholders are left intact.
SUGGESTED_FIXES: dict[str, dict[str, str]] = {
    "aos_cx": {
        "spanning-tree": (
            "interface {port}\n"
            "    spanning-tree bpdu-guard\n"
            "    spanning-tree port-type admin-edge"
        ),
        "poe priority": (
            "interface {port}\n"
            "    poe-allocate-by usage\n"
            "    poe priority high"
        ),
        "voice": (
            "interface {port}\n"
            "    vlan voice {voice_vlan}"
        ),
    },
    "ios": {
        "spanning-tree": (
            "interface {port}\n"
            "    spanning-tree portfast\n"
            "    spanning-tree bpduguard enable"
        ),
    },
    "ios_xe": {
        "spanning-tree": (
            "interface {port}\n"
            "    spanning-tree portfast\n"
            "    spanning-tree bpduguard enable"
        ),
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _safe_format(template: str, **kwargs) -> str:
    return template.format_map(_SafeDict(**kwargs))


def suggested_fix(platform: str, check_value: str, port: str) -> str:
    """Best-effort remediation snippet for a failing interface check."""
    table = SUGGESTED_FIXES.get((platform or "").lower(), {})
    key = (check_value or "").lower()
    for needle, tmpl in table.items():
        if needle in key or key in needle:
            return _safe_format(tmpl, port=port)
    return ""


def score_to_grade(score) -> str:
    if score is None:
        return "N/A"
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


# ── component 1: template compliance ─────────────────────────────────────────
def get_template_results(device) -> list:
    """Latest ComplianceTemplateResult per template for the device."""
    from .models import ComplianceTemplateResult
    latest: dict = {}
    for r in (ComplianceTemplateResult.objects
              .select_related("template")
              .filter(device=device)):
        if r.template_id not in latest:
            latest[r.template_id] = r
    return sorted(latest.values(), key=lambda r: r.template.name if r.template else "")


def get_template_score(results) -> float | None:
    scored = [r.score for r in results if r.score is not None]
    return round(sum(scored) / len(scored), 1) if scored else None


# ── component 2: interface rules ─────────────────────────────────────────────
def get_interface_rule_findings(device) -> list[dict]:
    """Per-interface results for the device, with config context + suggested fix."""
    from .interface_compliance import get_interface_config
    from .models import InterfaceComplianceResult

    out: list[dict] = []
    # Only results from ENABLED rules — a disabled rule's stale results must not
    # show on the device compliance tab or factor into the score.
    for r in (InterfaceComplianceResult.objects
              .select_related("rule")
              .filter(device=device, rule__enabled=True)):
        checks = r.findings or []
        iface_cfg = get_interface_config(device, r.interface)
        fix = ""
        for c in checks:
            if c.get("passed"):
                continue
            fix = suggested_fix(device.platform or "",
                                c.get("value") or c.get("description") or "",
                                r.interface)
            if fix:
                break
        out.append({
            "rule_name": r.rule.name if r.rule else "",
            "interface": r.interface,
            "neighbor": r.neighbor,
            "passed": r.passed,
            "passing": sum(1 for c in checks if c.get("passed")),
            "total": len(checks),
            "interface_config": iface_cfg,
            "findings": checks,
            "suggested_fix": fix,
        })
    out.sort(key=lambda x: (x["passed"], x["interface"]))
    return out


def get_interface_score(findings) -> float | None:
    if not findings:
        return None
    passing = sum(1 for f in findings if f["passed"])
    return round(passing / len(findings) * 100, 1)


# ── component 3: role consistency ────────────────────────────────────────────
def get_role_consistency_findings(device, role_cache=None) -> list[dict]:
    """This device's row from every enabled role-consistency rule that scopes it.

    ``role_cache`` (an optional dict) memoises each rule's full-group evaluation
    by rule id, so scoring a whole fleet doesn't re-run the same rule once per
    member device (O(N²) → O(N))."""
    from .models import RoleConsistencyRule
    from .role_consistency import run_role_consistency

    out: list[dict] = []
    for rule in RoleConsistencyRule.objects.filter(enabled=True):
        if rule.role_id and rule.role_id != device.role_id:
            continue
        if rule.platform and rule.platform != device.platform:
            continue
        if rule.site_id and rule.site_id != device.site_id:
            continue
        try:
            if role_cache is not None and rule.id in role_cache:
                res = role_cache[rule.id]
            else:
                res = run_role_consistency(rule, persist=False)
                if role_cache is not None:
                    role_cache[rule.id] = res
        except Exception as exc:  # noqa: BLE001 — one bad rule must not break the tab
            logger.warning("role-consistency %s failed for %s: %s", rule.name, device.hostname, exc)
            continue
        mine = next((r for r in res.get("results", []) if r.get("device_id") == device.id), None)
        if mine is None:
            continue
        out.append({
            "rule_name": rule.name,
            "check_type": rule.check_type,
            "passed": mine["status"] == "pass",
            "missing": mine.get("missing", []),
            "extra": mine.get("extra", []),
            "expected": mine.get("expected", []),
            "has": mine.get("has", []),
            "remediation": mine.get("remediation", ""),
        })
    return out


def get_role_score(findings) -> float | None:
    if not findings:
        return None
    passing = sum(1 for f in findings if f["passed"])
    return round(passing / len(findings) * 100, 1)


# ── component 4: running vs startup config ───────────────────────────────────
def get_startup_status(device) -> dict | None:
    """Latest running-vs-startup reconciliation for the device, or None if never checked."""
    from apps.configbackup.models import DeviceConfig
    cfg = (DeviceConfig.objects
           .filter(device=device, config_type=DeviceConfig.ConfigType.RUNNING)
           .exclude(startup_match__isnull=True)
           .order_by("-collected_at").first())
    if cfg is None:
        return None
    diff = cfg.startup_diff or ""
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return {
        "match": cfg.startup_match,
        "diff": diff,
        "added": added,
        "removed": removed,
        "checked_at": cfg.startup_checked_at.isoformat() if cfg.startup_checked_at else None,
    }


# ── overall ──────────────────────────────────────────────────────────────────
def calculate_device_compliance_score(device, role_cache=None) -> dict:
    """Weighted overall score + grade + per-component breakdown + findings.

    Pass a shared ``role_cache`` dict when scoring many devices (e.g. a fleet
    report) to evaluate each role-consistency rule once instead of per device."""
    template_results = get_template_results(device)
    template_score = get_template_score(template_results)
    iface_findings = get_interface_rule_findings(device)
    iface_score = get_interface_score(iface_findings)
    role_findings = get_role_consistency_findings(device, role_cache=role_cache)
    role_score = get_role_score(role_findings)
    startup = get_startup_status(device)

    breakdown: list[dict] = []
    weighted_sum = 0.0
    weight_total = 0.0

    if template_score is not None:
        breakdown.append({"name": "Template Compliance", "score": template_score, "weight": 50})
        weighted_sum += template_score * 0.5
        weight_total += 0.5
    if iface_score is not None:
        breakdown.append({
            "name": "Interface Rules", "score": iface_score, "weight": 30,
            "passing": sum(1 for f in iface_findings if f["passed"]),
            "total": len(iface_findings),
        })
        weighted_sum += iface_score * 0.3
        weight_total += 0.3
    if role_score is not None:
        breakdown.append({"name": "Role Consistency", "score": role_score, "weight": 20})
        weighted_sum += role_score * 0.2
        weight_total += 0.2
    # Running/startup mismatch is always a finding when checked — a mismatch
    # hard-penalises the score (weight 20, renormalised with the rest).
    if startup is not None:
        startup_score = 100.0 if startup["match"] else 0.0
        unsaved = startup["added"] + startup["removed"]
        breakdown.append({
            "name": "Running/Startup Match", "score": startup_score, "weight": 20,
            "match": startup["match"],
            "message": (
                "Running and startup configs match" if startup["match"]
                else f"WARNING: running config has {unsaved} line(s) not saved to "
                     "startup. Device will lose these changes on next reboot!"),
        })
        weighted_sum += startup_score * 0.2
        weight_total += 0.2

    overall = round(min(100.0, max(0.0, weighted_sum / weight_total)), 1) if weight_total else None

    return {
        "score": overall,
        "grade": score_to_grade(overall),
        "breakdown": breakdown,
        "template_score": template_score,
        "template_results": template_results,
        "interface_rule_findings": iface_findings,
        "role_consistency_findings": role_findings,
        "startup_status": startup,
    }
