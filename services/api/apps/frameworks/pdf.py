"""
PDF evidence-package generation (reportlab).

Renders a framework evaluation into an auditor-facing PDF: cover page with the
coverage score + control counts, then one row per control with its status,
finding and evidence bullets. reportlab is pure-Python (no system libs), and is
imported lazily so the rest of the app loads even if it isn't installed.
"""
from __future__ import annotations

import io

_STATUS_LABEL = {
    "satisfied": "SATISFIED", "partial": "PARTIAL", "gap": "GAP",
    "not_applicable": "N/A",
}
_STATUS_HEX = {
    "satisfied": "#16a34a", "partial": "#d97706", "gap": "#dc2626",
    "not_applicable": "#6b7280",
}


def build_evidence_pdf(report: dict, *, generated_at: str) -> bytes:
    """Render an evaluate_framework() result to PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

    fw = report["framework"]
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=18, spaceAfter=4)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6b7280"))
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, alignment=TA_LEFT)
    bullet = ParagraphStyle("bullet", parent=body, fontSize=8, leftIndent=10, textColor=colors.HexColor("#374151"))
    ctrl_title = ParagraphStyle("ct", parent=styles["Heading3"], fontSize=11, spaceBefore=10, spaceAfter=2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=f"{fw['name']} Evidence Package",
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    flow = []

    # Cover / header
    flow.append(Paragraph("spane — Regulatory Compliance Evidence Package", small))
    flow.append(Paragraph(f"{fw['name']} {fw.get('version') or ''}".strip(), h1))
    if fw.get("description"):
        flow.append(Paragraph(fw["description"], body))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(f"Generated: {generated_at}", small))
    flow.append(Spacer(1, 10))

    counts = report["counts"]
    cov = report["coverage"]
    summary_data = [
        ["Coverage", "Satisfied", "Partial", "Gap", "N/A", "Controls"],
        [f"{cov if cov is not None else '—'}%",
         counts.get("satisfied", 0), counts.get("partial", 0), counts.get("gap", 0),
         counts.get("not_applicable", 0), report["total_controls"]],
    ]
    summary = Table(summary_data, colWidths=[1.1 * inch] * 6)
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(summary)
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "Control catalog is a representative subset mapped to spane's collected evidence. "
        "Statuses marked PARTIAL may require manual attestation to fully satisfy.", small))
    flow.append(HRFlowable(width="100%", color=colors.HexColor("#e5e7eb")))

    # Controls
    for c in report["controls"]:
        status = c["status"]
        flow.append(Paragraph(f"{c['control_id']} — {c['title']}", ctrl_title))
        badge = (f'<font color="{_STATUS_HEX.get(status, "#000")}"><b>'
                 f'{_STATUS_LABEL.get(status, status.upper())}</b></font>')
        cat = f' · {c["category"]}' if c.get("category") else ""
        flow.append(Paragraph(f"{badge}{cat} — {c['summary']}", body))
        for line in c.get("evidence", []):
            flow.append(Paragraph(f"• {line}", bullet))

    doc.build(flow)
    return buf.getvalue()
