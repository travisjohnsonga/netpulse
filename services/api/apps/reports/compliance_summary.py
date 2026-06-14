"""
Compliance Summary report data builder.

Scores every (filtered) device with the weighted engine
(apps.compliance.device_score) and rolls the results up by site, role and
platform, plus a fleet summary, a findings breakdown by severity, and the
startup-config mismatch list (reboot risk).
"""
from __future__ import annotations

from collections import Counter, defaultdict

from django.utils import timezone

from apps.compliance.device_score import calculate_device_compliance_score, score_to_grade
from apps.devices.models import Device

# Pass/warn/fail thresholds on the 0-100 device score.
PASS_MIN = 70
WARN_MIN = 50


def _bucket(score):
    if score is None:
        return "not_checked"
    if score >= PASS_MIN:
        return "passing"
    if score >= WARN_MIN:
        return "warning"
    return "failing"


def _avg(scores):
    vals = [s for s in scores if s is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _device_findings(assessment) -> list[str]:
    """Short human findings for one device from its assessment."""
    out = []
    startup = assessment.get("startup_status")
    if startup and startup.get("match") is False:
        unsaved = startup.get("added", 0) + startup.get("removed", 0)
        out.append(f"startup config not saved ({unsaved} unsaved line(s))")
    for rf in assessment.get("role_consistency_findings", []):
        if not rf["passed"] and rf.get("missing"):
            out.append(f"missing VLANs: {', '.join(str(v) for v in rf['missing'])}")
    bad_ports = [f for f in assessment.get("interface_rule_findings", []) if not f["passed"]]
    if bad_ports:
        out.append(f"{len(bad_ports)} interface(s) failing rule checks")
    return out


def build_compliance_summary(*, site_ids=None, group_by=None,
                             include_score_breakdown=True, as_of=None) -> dict:
    group_by = group_by or ["site", "role", "platform"]
    qs = Device.objects.select_related("site", "role").exclude(
        status=Device.Status.DECOMMISSIONED)
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)
    devices = list(qs)

    # Score every device once. A shared role-consistency cache means each rule's
    # full-group evaluation runs once for the whole fleet, not once per device.
    scored = []
    role_cache: dict = {}
    for d in devices:
        a = calculate_device_compliance_score(d, role_cache=role_cache)
        scored.append({
            "device": d, "score": a["score"], "grade": a["grade"],
            "findings": _device_findings(a),
            "breakdown": a["breakdown"] if include_score_breakdown else None,
            "startup": a.get("startup_status"),
        })

    summary = {
        "total_devices": len(scored),
        "avg_score": _avg([s["score"] for s in scored]),
        "passing": sum(1 for s in scored if _bucket(s["score"]) == "passing"),
        "warning": sum(1 for s in scored if _bucket(s["score"]) == "warning"),
        "failing": sum(1 for s in scored if _bucket(s["score"]) == "failing"),
        "not_checked": sum(1 for s in scored if _bucket(s["score"]) == "not_checked"),
    }

    report = {
        "generated_at": (as_of or timezone.now()).isoformat(),
        "period": "as_of",
        "summary": summary,
    }

    def _group(key_fn, label_fn):
        groups = defaultdict(list)
        for s in scored:
            groups[key_fn(s["device"])].append(s)
        rows = []
        for key, members in groups.items():
            avg = _avg([m["score"] for m in members])
            issue_counter = Counter()
            for m in members:
                for f in m["findings"]:
                    # Normalise findings to issue buckets for top-issue rollups.
                    issue_counter[f.split("(")[0].strip()] += 1
            rows.append({
                "key": label_fn(key),
                "device_count": len(members),
                "avg_score": avg,
                "grade": score_to_grade(avg),
                "passing": sum(1 for m in members if _bucket(m["score"]) == "passing"),
                "failing": sum(1 for m in members if _bucket(m["score"]) == "failing"),
                "top_issues": [f"{n} device(s): {issue}" for issue, n in issue_counter.most_common(5)],
                "devices": [{
                    "hostname": m["device"].hostname,
                    "score": m["score"],
                    "grade": m["grade"],
                    "findings": m["findings"],
                } for m in sorted(members, key=lambda m: (m["score"] is None, m["score"] or 0))],
            })
        return sorted(rows, key=lambda r: (r["avg_score"] is None, r["avg_score"] or 0))

    if "site" in group_by:
        report["by_site"] = [
            {**r, "site": r.pop("key")} for r in
            _group(lambda d: d.site.name if d.site else "Unassigned", lambda k: k)]
    if "role" in group_by:
        report["by_role"] = [
            {**r, "role": r.pop("key")} for r in
            _group(lambda d: d.role.name if d.role else "Unassigned", lambda k: k)]
    if "platform" in group_by:
        report["by_platform"] = [
            {"platform": r["key"], "device_count": r["device_count"],
             "avg_score": r["avg_score"], "grade": r["grade"]} for r in
            _group(lambda d: d.platform or "unknown", lambda k: k)]

    # Findings by severity (failing devices = critical; warning = warning).
    critical, warning = [], []
    for s in scored:
        b = _bucket(s["score"])
        entry = {"hostname": s["device"].hostname, "score": s["score"], "findings": s["findings"]}
        if b == "failing":
            critical.append(entry)
        elif b == "warning":
            warning.append(entry)
    report["findings_summary"] = {"critical": critical, "warning": warning, "info": []}

    # Startup-config mismatches (reboot risk) — highlighted separately.
    report["startup_mismatch"] = [{
        "hostname": s["device"].hostname,
        "unsaved_lines": (s["startup"]["added"] + s["startup"]["removed"]) if s["startup"] else 0,
        "last_checked": s["startup"]["checked_at"] if s["startup"] else None,
    } for s in scored if s["startup"] and s["startup"].get("match") is False]

    return report
