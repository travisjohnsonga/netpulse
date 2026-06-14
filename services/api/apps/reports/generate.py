"""
Report generation orchestration — build data, render to a format, persist.

Single seam used by the API views and the scheduled-delivery task so on-demand
and scheduled reports are produced identically.
"""
from __future__ import annotations

from . import render
from .compliance_summary import build_compliance_summary
from .daily_ops import build_ops_report
from .models import ReportType
from .storage import store_report

# report_type → {build, formats: {fmt: renderer}}
_SPEC = {
    ReportType.COMPLIANCE_SUMMARY: {
        "build": build_compliance_summary,
        "formats": {
            "pdf": render.compliance_summary_pdf,
            "csv": render.compliance_summary_csv,
            "json": render.to_json,
        },
    },
    ReportType.DAILY_OPS: {
        "build": build_ops_report,
        "formats": {
            "pdf": render.daily_ops_pdf,
            "csv": render.daily_ops_csv,
            "html": render.daily_ops_html,
            "json": render.to_json,
        },
    },
}


def supported_formats(report_type: str) -> list[str]:
    spec = _SPEC.get(report_type)
    return list(spec["formats"].keys()) if spec else []


def _build_kwargs(report_type: str, params: dict) -> dict:
    if report_type == ReportType.COMPLIANCE_SUMMARY:
        return {
            "site_ids": params.get("site_ids") or None,
            "group_by": params.get("group_by") or ["site", "role", "platform"],
            "include_score_breakdown": params.get("include_score_breakdown", True),
        }
    if report_type == ReportType.DAILY_OPS:
        return {"period": params.get("period") or "daily",
                "end_date": params.get("end_date") or params.get("date"),
                "site_ids": params.get("site_ids") or None}
    return {}


def generate(report_type: str, fmt: str, params: dict, *, user=None, source="on-demand"):
    """Build + render + store. Returns (GeneratedReport, content_bytes, data)."""
    spec = _SPEC.get(report_type)
    if spec is None:
        raise ValueError(f"Unknown report type: {report_type}")
    if fmt not in spec["formats"]:
        raise ValueError(f"Format {fmt!r} not supported for {report_type} "
                         f"(supported: {', '.join(spec['formats'])})")
    data = spec["build"](**_build_kwargs(report_type, params))
    content = spec["formats"][fmt](data)
    report = store_report(report_type=report_type, fmt=fmt, content=content,
                          params=params, user=user, source=source)
    return report, content if isinstance(content, bytes) else content.encode("utf-8"), data
