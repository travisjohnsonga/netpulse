"""
Framework evaluation: run every control's evidence collector and roll the
results up into a coverage score.

Coverage = weighted mean of control status scores (satisfied=1, partial=0.5,
gap=0), excluding not-applicable controls, expressed as a percentage.
"""
from __future__ import annotations

from .evidence import NOT_APPLICABLE, STATUS_SCORE, evaluate_control


def evaluate_framework(framework) -> dict:
    """Evaluate one framework. Returns summary + per-control evidence."""
    controls = list(framework.controls.all())
    evaluated = []
    weighted_score = 0.0
    weight_total = 0
    counts = {"satisfied": 0, "partial": 0, "gap": 0, "not_applicable": 0}

    for control in controls:
        ev = evaluate_control(control.mapping_key)
        status = ev["status"]
        counts[status] = counts.get(status, 0) + 1
        if status != NOT_APPLICABLE:
            weighted_score += STATUS_SCORE.get(status, 0.0) * control.weight
            weight_total += control.weight
        evaluated.append({
            "control_id": control.control_id,
            "title": control.title,
            "description": control.description,
            "category": control.category,
            "mapping_key": control.mapping_key,
            "weight": control.weight,
            "status": status,
            "summary": ev["summary"],
            "metrics": ev["metrics"],
            "evidence": ev["evidence"],
        })

    coverage = round(weighted_score / weight_total * 100, 1) if weight_total else None
    return {
        "framework": {
            "key": framework.key, "name": framework.name,
            "description": framework.description, "version": framework.version,
        },
        "coverage": coverage,
        "counts": counts,
        "total_controls": len(controls),
        "controls": evaluated,
    }


def framework_summary(framework) -> dict:
    """Lightweight roll-up (coverage + counts) without the full control list."""
    full = evaluate_framework(framework)
    return {
        "key": framework.key,
        "name": framework.name,
        "description": framework.description,
        "version": framework.version,
        "coverage": full["coverage"],
        "counts": full["counts"],
        "total_controls": full["total_controls"],
    }
