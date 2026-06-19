"""
Report renderers — turn a report data dict into PDF / CSV / HTML / JSON bytes.

PDFs use reportlab (pure-Python, already a dependency) with spane branding:
a coloured header band, alternating-row tables, colour-coded severity, and a
simple score bar. No matplotlib dependency.
"""
from __future__ import annotations

import csv
import io
import json

# spane brand palette.
BRAND = "#2563eb"      # blue-600
INK = "#1f2937"        # gray-800
MUTED = "#6b7280"      # gray-500
GREEN = "#16a34a"
AMBER = "#d97706"
RED = "#dc2626"

# Daily Operations report palette (richer, for the redesigned multi-page PDF).
NAVY = "#1a1a2e"       # section header / page chrome
ACCENT = "#4f86c6"     # spane blue accent
SUCCESS = "#27ae60"
WARNING = "#f39c12"
ERROR = "#e74c3c"
TEXT = "#2c3e50"
LIGHT = "#ecf0f1"
WHITE = "#ffffff"


def _grade_box_color(grade):
    """Colour for a letter-grade chip: A green, B blue, C orange, D/F red."""
    return {"A": SUCCESS, "B": ACCENT, "C": WARNING, "D": ERROR, "F": "#922b21"}.get(
        grade or "", MUTED)


def _hex(colors, h):
    return colors.HexColor(h)


def grade_color(score):
    if score is None:
        return MUTED
    if score >= 70:
        return GREEN
    if score >= 50:
        return AMBER
    return RED


# ── shared PDF scaffolding ───────────────────────────────────────────────────
def _doc(title):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=title,
                            leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    return buf, doc


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    s = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=s["Heading1"], fontSize=18, textColor=_hex(colors, INK), spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontSize=13, textColor=_hex(colors, BRAND), spaceBefore=12, spaceAfter=4),
        "body": ParagraphStyle("body", parent=s["Normal"], fontSize=9, textColor=_hex(colors, INK)),
        "small": ParagraphStyle("small", parent=s["Normal"], fontSize=8, textColor=_hex(colors, MUTED)),
    }


def _header(flow, styles, title, subtitle):
    from reportlab.lib import colors
    from reportlab.platypus import HRFlowable, Paragraph, Spacer
    flow.append(Paragraph("spane — unified infrastructure visibility", styles["small"]))
    flow.append(Paragraph(title, styles["h1"]))
    if subtitle:
        flow.append(Paragraph(subtitle, styles["body"]))
    flow.append(Spacer(1, 6))
    flow.append(HRFlowable(width="100%", thickness=2, color=_hex(colors, BRAND)))
    flow.append(Spacer(1, 8))


def _table(data, col_widths=None, header_bg=INK):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _hex(colors, header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, _hex(colors, "#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _hex(colors, "#f3f4f6")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def _score_bar(score, width=200):
    """A simple coloured score bar Drawing (0-100)."""
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    d = Drawing(width + 60, 18)
    d.add(Rect(0, 4, width, 10, fillColor=_hex(colors, "#e5e7eb"), strokeColor=None))
    val = 0 if score is None else max(0, min(100, score))
    d.add(Rect(0, 4, width * val / 100, 10, fillColor=_hex(colors, grade_color(score)), strokeColor=None))
    d.add(String(width + 6, 5, f"{'—' if score is None else round(score)}/100",
                 fontSize=9, fillColor=_hex(colors, INK)))
    return d


def _diff_table(diff_text, max_lines=90, max_cols=110):
    """A colour-coded unified-diff block: green adds, red removes, grey context."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    lines = (diff_text or "").splitlines()
    if not lines:
        return None
    truncated = len(lines) > max_lines
    lines = lines[:max_lines]
    rows = [[(ln[:max_cols] if ln else " ")] for ln in lines]
    if truncated:
        rows.append([f"… (diff truncated at {max_lines} lines)"])
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Courier"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
        ("BOX", (0, 0), (-1, -1), 0.4, _hex(colors, "#d1d5db")),
    ]
    for i, ln in enumerate(lines):
        if ln.startswith("+") and not ln.startswith("+++"):
            style += [("BACKGROUND", (0, i), (0, i), _hex(colors, "#dcfce7")),
                      ("TEXTCOLOR", (0, i), (0, i), _hex(colors, "#166534"))]
        elif ln.startswith("-") and not ln.startswith("---"):
            style += [("BACKGROUND", (0, i), (0, i), _hex(colors, "#fee2e2")),
                      ("TEXTCOLOR", (0, i), (0, i), _hex(colors, "#991b1b"))]
        elif ln.startswith("@@"):
            style.append(("TEXTCOLOR", (0, i), (0, i), _hex(colors, BRAND)))
        else:
            style += [("BACKGROUND", (0, i), (0, i), _hex(colors, "#f9fafb")),
                      ("TEXTCOLOR", (0, i), (0, i), _hex(colors, MUTED))]
    t = Table(rows, colWidths=[480])
    t.setStyle(TableStyle(style))
    return t


def _page_footer(canvas, doc):
    from reportlab.lib import colors
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_hex(colors, MUTED))
    canvas.drawString(0.7 * 72, 0.4 * 72, "Generated by spane")
    canvas.drawRightString(7.8 * 72, 0.4 * 72, f"Page {doc.page}")
    canvas.restoreState()


# ── Compliance Summary ───────────────────────────────────────────────────────
def compliance_summary_pdf(data: dict) -> bytes:
    from reportlab.platypus import Paragraph, Spacer
    styles = _styles()
    buf, doc = _doc("spane Compliance Summary")
    flow = []
    gen = (data.get("generated_at") or "")[:19].replace("T", " ")
    _header(flow, styles, "Compliance Summary Report", f"Generated {gen} UTC")

    s = data["summary"]
    flow.append(Paragraph("Fleet Compliance", styles["h2"]))
    flow.append(_score_bar(s["avg_score"], width=260))
    flow.append(Spacer(1, 6))
    flow.append(_table([
        ["Total", "Passing", "Warning", "Failing", "Not checked", "Avg score"],
        [s["total_devices"], s["passing"], s["warning"], s["failing"], s["not_checked"],
         "—" if s["avg_score"] is None else s["avg_score"]],
    ]))

    if data.get("startup_mismatch"):
        flow.append(Paragraph("⚠ Startup Config Issues (reboot risk)", styles["h2"]))
        rows = [["Device", "Unsaved lines", "Last checked"]]
        rows += [[m["hostname"], m["unsaved_lines"], (m["last_checked"] or "")[:19].replace("T", " ")]
                 for m in data["startup_mismatch"]]
        flow.append(_table(rows, header_bg=RED))

    for group, label, cols in (("by_site", "By Site", "site"),
                               ("by_role", "By Role", "role")):
        if data.get(group):
            flow.append(Paragraph(label, styles["h2"]))
            rows = [[cols.title(), "Devices", "Avg", "Grade", "Pass", "Fail", "Top issues"]]
            for r in data[group]:
                rows.append([r[cols], r["device_count"],
                             "—" if r["avg_score"] is None else r["avg_score"], r["grade"],
                             r["passing"], r["failing"], "; ".join(r["top_issues"][:3]) or "—"])
            flow.append(_table(rows, col_widths=[70, 42, 32, 36, 30, 30, 200]))

    if data.get("by_platform"):
        flow.append(Paragraph("By Platform", styles["h2"]))
        rows = [["Platform", "Devices", "Avg score", "Grade"]]
        rows += [[r["platform"], r["device_count"],
                  "—" if r["avg_score"] is None else r["avg_score"], r["grade"]]
                 for r in data["by_platform"]]
        flow.append(_table(rows))

    crit = data.get("findings_summary", {}).get("critical", [])
    if crit:
        flow.append(Paragraph("Failing Devices — Findings", styles["h2"]))
        rows = [["Device", "Score", "Findings"]]
        rows += [[c["hostname"], "—" if c["score"] is None else c["score"],
                  "; ".join(c["findings"]) or "—"] for c in crit]
        flow.append(_table(rows, col_widths=[120, 40, 300], header_bg=RED))

    doc.build(flow, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


class _SafeCsvWriter:
    """csv.writer that runs every cell through csv_safe (formula-injection guard)
    — report CSVs carry device hostnames / finding text influenced by inventory."""

    def __init__(self, fileobj):
        from apps.core.audit import csv_safe
        self._w = csv.writer(fileobj)
        self._safe = csv_safe

    def writerow(self, row):
        self._w.writerow([self._safe(c) for c in row])


def compliance_summary_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = _SafeCsvWriter(out)
    w.writerow(["group_type", "group", "device_count", "avg_score", "grade", "passing", "failing"])
    for r in data.get("by_site", []):
        w.writerow(["site", r["site"], r["device_count"], r["avg_score"], r["grade"], r["passing"], r["failing"]])
    for r in data.get("by_role", []):
        w.writerow(["role", r["role"], r["device_count"], r["avg_score"], r["grade"], r["passing"], r["failing"]])
    for r in data.get("by_platform", []):
        w.writerow(["platform", r["platform"], r["device_count"], r["avg_score"], r["grade"], "", ""])
    w.writerow([])
    w.writerow(["hostname", "score", "grade", "findings"])
    for r in data.get("by_site", []):
        for d in r.get("devices", []):
            w.writerow([d["hostname"], d["score"], d["grade"], "; ".join(d["findings"])])
    return out.getvalue().encode("utf-8")


# ── Daily Operations ─────────────────────────────────────────────────────────
def _daily_styles():
    """Typography for the Daily Operations report (separate from the shared set)."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    base = getSampleStyleSheet()["Normal"]

    def st(name, **kw):
        kw.setdefault("fontName", "Helvetica")
        if "textColor" in kw:
            kw["textColor"] = _hex(colors, kw["textColor"])
        return ParagraphStyle(name, parent=base, **kw)

    return {
        "title": st("d_title", fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, spaceAfter=2),
        "subtitle": st("d_sub", fontSize=11, textColor=TEXT, spaceAfter=1),
        "body": st("d_body", fontSize=9, textColor=TEXT, spaceAfter=2),
        "small": st("d_small", fontSize=8, textColor=MUTED, spaceAfter=1),
        "mono": st("d_mono", fontName="Courier", fontSize=8, textColor=TEXT),
        "note": st("d_note", fontSize=8.5, textColor=ERROR, spaceBefore=2, spaceAfter=2),
        "box_title": st("d_boxt", fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE, alignment=1),
        "box_big": st("d_boxb", fontName="Helvetica-Bold", fontSize=20, textColor=TEXT, alignment=1),
        "box_sub": st("d_boxs", fontSize=7.5, textColor=TEXT, alignment=1),
    }


def _content_width():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    return letter[0] - 1.4 * inch  # matches the 0.7in L/R margins


def _section(flow, styles, text):
    """A dark navy full-width section header bar with white bold text."""
    from reportlab.lib import colors
    from reportlab.platypus import Spacer, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    flow.append(Spacer(1, 8))
    p = Paragraph(text, ParagraphStyle("sh", fontName="Helvetica-Bold", fontSize=11,
                                       textColor=_hex(colors, WHITE)))
    t = Table([[p]], colWidths=[_content_width()])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _hex(colors, NAVY)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 6))


def _dtable(data, col_widths=None, header_bg=ACCENT):
    """Daily-report table: accent header, alternating rows, light borders."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _hex(colors, header_bg)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), _hex(colors, TEXT)),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, _hex(colors, "#d5dbdb")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _hex(colors, LIGHT)]),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _stat_box(styles, title, big, sub_lines, color):
    """One coloured summary box (title band + big number + sub lines)."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle
    rows = [[Paragraph(title, styles["box_title"])], [Paragraph(big, styles["box_big"])]]
    rows += [[Paragraph(ln, styles["box_sub"])] for ln in sub_lines]
    t = Table(rows, colWidths=[1.55 * 72])
    style = [
        ("BACKGROUND", (0, 0), (0, 0), _hex(colors, color)),
        ("BACKGROUND", (0, 1), (0, -1), _hex(colors, "#fbfcfc")),
        ("BOX", (0, 0), (-1, -1), 1, _hex(colors, color)),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (0, 0), 4), ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 1), (0, 1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, -1), (0, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def _page_decorator(header_label, generated):
    """Return an onPage(canvas, doc) drawing the branded header + footer."""
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.pagesizes import letter

    def _draw(canvas, doc):
        w, h = letter
        canvas.saveState()
        # Header
        canvas.setFont("Helvetica-Bold", 11)
        canvas.setFillColor(_hex(colors, ACCENT))
        canvas.drawString(0.7 * inch, h - 0.45 * inch, "spane")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_hex(colors, MUTED))
        canvas.drawRightString(w - 0.7 * inch, h - 0.45 * inch,
                               f"{header_label} | Page {doc.page}")
        canvas.setStrokeColor(_hex(colors, ACCENT))
        canvas.setLineWidth(1)
        canvas.line(0.7 * inch, h - 0.52 * inch, w - 0.7 * inch, h - 0.52 * inch)
        # Footer
        canvas.setStrokeColor(_hex(colors, "#d5dbdb"))
        canvas.setLineWidth(0.5)
        canvas.line(0.7 * inch, 0.55 * inch, w - 0.7 * inch, 0.55 * inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(_hex(colors, MUTED))
        canvas.drawString(0.7 * inch, 0.4 * inch, f"Generated by spane | {generated}")
        canvas.drawRightString(w - 0.7 * inch, 0.4 * inch, "CONFIDENTIAL — Internal Use Only")
        canvas.restoreState()

    return _draw


def _outage_timeline(av, styles):
    """A 24h timeline Drawing with one bar per outage, positioned by time of day."""
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    outages = av.get("went_down") or []
    width = _content_width()
    left, axis_w = 6, width - 12
    row_h, top_pad = 11, 18
    d = Drawing(width, top_pad + row_h * max(1, len(outages)) + 6)
    # Axis
    y0 = d.height - 10
    d.add(Line(left, y0, left + axis_w, y0, strokeColor=_hex(colors, MUTED), strokeWidth=0.5))
    for hh in (0, 6, 12, 18, 24):
        x = left + axis_w * hh / 24
        d.add(Line(x, y0 - 2, x, y0 + 2, strokeColor=_hex(colors, MUTED), strokeWidth=0.5))
        d.add(String(x - 6, y0 + 4, f"{hh:02d}:00", fontSize=6, fillColor=_hex(colors, MUTED)))

    def _mins(iso):
        # minutes-into-day from an ISO timestamp's HH:MM (UTC).
        try:
            hh, mm = int(iso[11:13]), int(iso[14:16])
            return hh * 60 + mm
        except (TypeError, ValueError, IndexError):
            return None

    for i, o in enumerate(outages[:12]):
        y = y0 - top_pad - i * row_h
        s = _mins(o.get("down_at"))
        e = _mins(o.get("recovered_at")) if o.get("recovered_at") else 24 * 60
        if s is None:
            continue
        x1 = left + axis_w * s / 1440
        x2 = left + axis_w * min(1440, max(e, s + 4)) / 1440
        d.add(Rect(x1, y, max(2, x2 - x1), row_h - 3, fillColor=_hex(colors, ERROR), strokeColor=None))
        d.add(String(left, y + 1, (o.get("hostname") or "")[:22], fontSize=6,
                     fillColor=_hex(colors, TEXT)))
        d.add(String(min(left + axis_w - 40, x2 + 3), y + 1, f"{o.get('duration_minutes', 0)}m",
                     fontSize=6, fillColor=_hex(colors, MUTED)))
    return d


def _trend_chart(days, values, color_hex, kind="line"):
    """A small line/bar trend chart Drawing, or None on any error / no data."""
    try:
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics.charts.linecharts import HorizontalLineChart
        from reportlab.lib import colors
        vals = [float(v) if v is not None else 0.0 for v in values]
        if not vals:
            return None
        width, height = _content_width(), 95
        d = Drawing(width, height)
        ch = VerticalBarChart() if kind == "bar" else HorizontalLineChart()
        ch.x, ch.y = 28, 18
        ch.width, ch.height = width - 45, height - 30
        ch.data = [vals]
        names = [k[5:] for k in days]  # MM-DD
        step = max(1, len(names) // 8)
        ch.categoryAxis.categoryNames = [n if i % step == 0 else "" for i, n in enumerate(names)]
        ch.categoryAxis.labels.fontSize = 6
        ch.valueAxis.labels.fontSize = 6
        ch.valueAxis.valueMin = 0
        if kind == "bar":
            ch.bars[0].fillColor = _hex(colors, color_hex)
        else:
            ch.lines[0].strokeColor = _hex(colors, color_hex)
            ch.lines[0].strokeWidth = 1.5
        d.add(ch)
        return d
    except Exception:  # noqa: BLE001 — charts must never break report generation
        return None


def daily_ops_pdf(data: dict) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)
    styles = _daily_styles()
    buf = io.BytesIO()
    rdate = data["report_date"]
    gen = (data.get("generated_at") or "")[:16].replace("T", " ")
    doc = SimpleDocTemplate(
        buf, pagesize=letter, title="spane Daily Operations",
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.85 * inch, bottomMargin=0.75 * inch)

    sec = data["security_events"]
    av = data["device_availability"]
    ce = data["compliance_events"]
    svc = data.get("service_checks", {})
    cc = data["config_changes"]
    ch = data["collection_health"]
    ah = data["agent_health"]
    al = data["alerts_summary"]
    sp = data.get("spane_access_events", {})

    flow = []

    # ── Page 1: Executive summary ────────────────────────────────────────────
    title = data.get("report_title", "Daily Operations Report")
    date_label = data.get("date_label", rdate)
    flow.append(Paragraph(title, styles["title"]))
    flow.append(Paragraph(f"{date_label} — All Sites", styles["subtitle"]))
    flow.append(Paragraph(f"Generated {gen} UTC", styles["small"]))
    flow.append(Spacer(1, 12))

    # Four colored stat boxes across.
    sec_color = ERROR if sec.get("total_failures") else SUCCESS
    av_color = ERROR if av.get("total_outages") else SUCCESS
    score = ce.get("fleet_avg_today")
    comp_color = _grade_box_color(ce.get("fleet_grade"))
    al_color = ERROR if al.get("critical") else (WARNING if al.get("total") else SUCCESS)
    boxes = [
        _stat_box(styles, "SECURITY", str(sec.get("total_failures", 0)),
                  [f"{sec.get('unique_sources', 0)} sources", f"{sec.get('device_count', 0)} devices"],
                  sec_color),
        _stat_box(styles, "AVAILABILITY", f"{av.get('availability_pct', 100)}%",
                  [f"{av.get('total_outages', 0)} outages", f"{av.get('total_downtime_minutes', 0)} min down"],
                  av_color),
        _stat_box(styles, "COMPLIANCE", f"{score}/100" if score is not None else "—",
                  [f"Grade {ce.get('fleet_grade') or '—'}", f"{ce.get('unsaved_configs', 0)} unsaved"],
                  comp_color),
        _stat_box(styles, "ALERTS", str(al.get("total", 0)),
                  [f"{al.get('critical', 0)} critical", f"{al.get('low', 0)} low"], al_color),
    ]
    grid = Table([boxes], colWidths=[_content_width() / 4] * 4)
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    flow.append(grid)
    flow.append(Spacer(1, 14))

    # One-line section summaries.
    def _ok(label):
        return f"<font color='{SUCCESS}'>✔</font> {label}"

    def _warn(label):
        return f"<font color='{ERROR}'>!</font> {label}"

    flow.append(Paragraph("Summary", styles["subtitle"]))
    sec_line = (f"{sec['total_failures']} auth failures, {sec['unique_sources']} sources"
                if sec.get("total_failures") else "No device auth failures")
    av_line = (f"{av['total_outages']} outages, {av['total_downtime_minutes']} min downtime"
               if av.get("total_outages") else "No outages")
    comp_line = (f"{ce['total_failing_devices']} below threshold, {ce['unsaved_configs']} unsaved configs"
                 if (ce.get("total_failing_devices") or ce.get("unsaved_configs")) else "Fleet compliant")
    svc_line = (f"{svc.get('total_passing', 0)} passed, {svc.get('total_failures', 0)} failed"
                if svc.get("configured") else "No service checks configured")
    cc_line = f"{len(cc)} change(s) detected" if cc else "No config changes"
    coll_line = (f"{ch['successful']}/{ch['total_attempts']} collected ({ch.get('success_rate', 0)}%)"
                 if ch.get("total_attempts") else "No collection attempts logged")
    ah_line = f"{ah['online']}/{ah['total_agents']} agents online"
    sp_line = (f"{sp.get('total_failures', 0)} failed logins, "
               f"{len(sp.get('after_hours_logins', []))} after-hours")
    for n, (line, bad) in enumerate([
        (sec_line, sec.get("total_failures")), (av_line, av.get("total_outages")),
        (comp_line, ce.get("total_failing_devices") or ce.get("unsaved_configs")),
        (svc_line, svc.get("total_failures")), (cc_line, len(cc)),
        (coll_line, ch.get("failed")), (ah_line, ah.get("offline")),
        (sp_line, sp.get("total_failures")),
    ], start=1):
        flow.append(Paragraph(f"{n}. " + (_warn(line) if bad else _ok(line)), styles["body"]))
    # Period-over-period comparison line (multi-day periods).
    comp = data.get("comparison")
    if comp:
        def _delta(pct):
            if pct is None:
                return ""
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
            return f" ({arrow} {abs(pct)}% vs prev)"
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(
            f"vs previous period — auth failures {comp['security_failures_prev']}"
            f"{_delta(comp['security_failures_change_pct'])}; outages {comp['outages_prev']}"
            f"{_delta(comp['outages_change_pct'])}; downtime {comp['downtime_prev']} min"
            f"{_delta(comp['downtime_change_pct'])}.", styles["small"]))

    # Trend charts (multi-day periods).
    trends = data.get("trends")
    if trends and trends.get("days"):
        flow.append(Spacer(1, 8))
        flow.append(Paragraph("Trends", styles["subtitle"]))
        days = trends["days"]
        for label, series, color, kind in [
            ("Availability — downtime minutes/day", trends.get("availability_downtime"), ACCENT, "line"),
            ("Compliance — avg fleet score/day", trends.get("compliance"), SUCCESS, "line"),
            ("Security — auth failures/day", trends.get("security"), ERROR, "bar"),
        ]:
            chart = _trend_chart(days, series or [], color, kind)
            if chart is not None:
                flow.append(Paragraph(label, styles["small"]))
                flow.append(chart)
                flow.append(Spacer(1, 6))

    flow.append(Spacer(1, 8))
    flow.append(Paragraph("See following pages for details.", styles["small"]))

    # ── Detail pages (conditional) ───────────────────────────────────────────
    # Security — only if failures.
    if sec.get("total_failures"):
        flow.append(PageBreak())
        _section(flow, styles, "1 · Device Security Events")
        flow.append(Paragraph(
            f"<b>{sec['total_failures']}</b> authentication failure(s) from "
            f"<b>{sec['unique_sources']}</b> source(s) across <b>{sec.get('device_count', 0)}</b> "
            f"device(s).", styles["body"]))
        if sec.get("groups"):
            rows = [["Username", "Source IP / Devices", "Count", "Time Range"]]
            for g in sec["groups"][:25]:
                srcs = ", ".join(g.get("source_ips") or []) or "—"
                if g.get("device_count", 0) > 1:
                    srcs = f"{srcs}\n({g['device_count']} devices)"
                rows.append([Paragraph(g.get("username") or "(unknown)", styles["mono"]),
                             Paragraph(srcs, styles["small"]), g["count"], g.get("time_range", "")])
            flow.append(_dtable(rows, col_widths=[130, 230, 45, 90], header_bg=ERROR))
        for fl in sec.get("flags", [])[:10]:
            flow.append(Paragraph(fl, styles["note"]))
        # Affected device list for the top group.
        if sec.get("groups") and sec["groups"][0].get("device_count", 0) > 1:
            devs = ", ".join(sec["groups"][0].get("devices") or [])
            flow.append(Paragraph(f"Affected devices: {devs}", styles["small"]))
        for s in (sec.get("success_after_failures") or [])[:10]:
            flow.append(Paragraph(
                f"⚠️ SUCCESS AFTER FAILURES: {s['username']} failed {s['fail_count']} time(s) then "
                f"succeeded at {s.get('at', '')} on {s.get('device') or 'unknown'} — review immediately.",
                styles["note"]))

    # Availability — only if outages.
    if av.get("total_outages"):
        flow.append(PageBreak())
        _section(flow, styles, "2 · Device Availability")
        flow.append(Paragraph(
            f"<b>{av['total_outages']}</b> outage(s), {av['total_downtime_minutes']} min total "
            f"downtime · fleet availability {av['availability_pct']}%.", styles["body"]))
        flow.append(Spacer(1, 4))
        flow.append(_outage_timeline(av, styles))
        flow.append(Spacer(1, 6))
        rows = [["Device", "Down At", "Recovered", "Dur (min)", "Site"]]
        for o in av["went_down"][:40]:
            rows.append([o["hostname"], (o["down_at"] or "")[11:19],
                         (o["recovered_at"] or "still down")[11:19] if o.get("recovered_at") else "still down",
                         o["duration_minutes"], o.get("site") or ""])
        flow.append(_dtable(rows, col_widths=[150, 80, 90, 70, 110], header_bg=ERROR))

    # Compliance — always.
    flow.append(PageBreak())
    _section(flow, styles, "3 · Compliance Status")
    if score is not None:
        trend = ""
        if ce.get("fleet_avg_delta") is not None and ce.get("fleet_avg_prev") is not None:
            arrow = "↑" if ce["fleet_avg_delta"] > 0 else ("↓" if ce["fleet_avg_delta"] < 0 else "→")
            trend = f"  {arrow} from {ce['fleet_avg_prev']} previous day"
        flow.append(Paragraph(
            f"<b>Fleet score: {score}/100  Grade {ce.get('fleet_grade') or '—'}</b>{trend}",
            styles["body"]))
    else:
        flow.append(Paragraph("No stored compliance scores yet.", styles["body"]))
    if ce.get("unsaved_devices"):
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(
            f"<b>{len(ce['unsaved_devices'])} device(s) with unsaved configurations</b>", styles["body"]))
        rows = [["Device", "Site", "Last Checked"]]
        rows += [[u["hostname"], u.get("site") or "", (u.get("last_checked") or "")[:16].replace("T", " ")]
                 for u in ce["unsaved_devices"][:30]]
        flow.append(_dtable(rows, col_widths=[180, 130, 130], header_bg=WARNING))
        flow.append(Paragraph(
            "⚠️ Action required: run 'write memory' on the listed devices — unsaved changes "
            "are lost on reboot.", styles["note"]))
    if ce.get("failing_devices"):
        flow.append(Spacer(1, 6))
        flow.append(Paragraph("Devices below compliance threshold (&lt;70)", styles["body"]))
        rows = [["Device", "Score", "Grade", "Top Issues"]]
        for d in ce["failing_devices"]:
            rows.append([d["hostname"], d["score"], d["grade"],
                         Paragraph("; ".join(d.get("top_issues") or []) or "—", styles["small"])])
        flow.append(_dtable(rows, col_widths=[150, 50, 45, 195], header_bg=ERROR))
    if ce.get("degraded") or ce.get("improved"):
        flow.append(Spacer(1, 4))
        flow.append(Paragraph("Score changes from previous day", styles["body"]))
        for d in ce.get("degraded", [])[:10]:
            flow.append(Paragraph(
                f"<font color='{ERROR}'>↓</font> {d['hostname']} ({d['score_prev']}→{d['score_today']})",
                styles["small"]))
        for d in ce.get("improved", [])[:10]:
            flow.append(Paragraph(
                f"<font color='{SUCCESS}'>↑</font> {d['hostname']} ({d['score_prev']}→{d['score_today']})",
                styles["small"]))

    # Service checks — only if failures.
    if svc.get("configured") and svc.get("total_failures"):
        flow.append(PageBreak())
        _section(flow, styles, "4 · Service Check Failures")
        flow.append(Paragraph(
            f"{svc['total_executions']} execution(s): {svc['total_passing']} passed"
            f"{f' ({svc['pass_rate']}%)' if svc.get('pass_rate') is not None else ''}, "
            f"<b>{svc['total_failures']} failed</b> across {svc['affected_checks']} check(s).",
            styles["body"]))
        rows = [["Check", "Device", "Type", "Fails", "Avg Dur", "Window"]]
        for s in svc["summaries"][:25]:
            dur = f"{s['avg_duration_s']}s" if s.get("avg_duration_s") is not None else "—"
            win = f"{(s['first_failure'] or '')[11:16]}–{(s['last_failure'] or '')[11:16]}"
            rows.append([s["check_name"][:24], (s["device"] or "")[:16], s["check_type"],
                         s["failure_count"], dur, win])
        flow.append(_dtable(rows, col_widths=[140, 110, 50, 45, 60, 95], header_bg=ERROR))
        for s in svc["summaries"][:25]:
            if s.get("correlated_outage"):
                co = s["correlated_outage"]
                flow.append(Paragraph(
                    f"⟲ Correlated with device outage: {co['hostname']} unreachable "
                    f"{(co['down_at'] or '')[11:16]}–{(co.get('recovered_at') or 'now')[11:16]} "
                    f"(matches {s['check_name']}).", styles["small"]))

    # Config changes — only if any.
    if cc:
        flow.append(PageBreak())
        _section(flow, styles, "5 · Configuration Changes")
        rows = [["Device", "+", "-", "Detected"]]
        rows += [[c["hostname"], f"+{c['lines_added']}", f"-{c['lines_removed']}",
                  (c["detected_at"] or "")[11:19]] for c in cc]
        flow.append(_dtable(rows, col_widths=[230, 50, 50, 90]))
        flow.append(Spacer(1, 8))
        for c in cc:
            prev = (c.get("previous_backup_at") or "")[11:19]
            head = f"{c['hostname']} — change at {(c['detected_at'] or '')[11:19]}"
            if prev:
                head += f"  ·  previous backup {prev}"
            flow.append(Paragraph(head, styles["small"]))
            tbl = _diff_table(c.get("diff") or "")
            flow.append(tbl if tbl is not None else Paragraph("(no diff available)", styles["small"]))
            flow.append(Spacer(1, 8))

    # Collection health — only if failures.
    if ch.get("failed"):
        flow.append(PageBreak())
        _section(flow, styles, "6 · Collection Health")
        flow.append(Paragraph(
            f"{ch['total_attempts']} attempt(s) across {ch.get('device_count', 0)} device(s) — "
            f"{ch.get('success_rate', 0)}% success.", styles["body"]))
        if ch.get("by_status"):
            rows = [["Status", "Count", "Rate"]]
            rows += [[s["status"], s["count"], f"{s['rate']}%"] for s in ch["by_status"]]
            flow.append(_dtable(rows, col_widths=[180, 90, 90]))
        if ch.get("failed_devices"):
            flow.append(Spacer(1, 4))
            rows = [["Device", "Error", "Attempts"]]
            rows += [[f["hostname"], f["error"], f["attempts"]] for f in ch["failed_devices"][:40]]
            flow.append(_dtable(rows, header_bg=ERROR))

    # Alerts — only if any.
    if al.get("total"):
        flow.append(PageBreak())
        _section(flow, styles, "7 · Alerts")
        flow.append(Paragraph(
            f"<b>{al['total']}</b> alert(s): {al['critical']} critical, {al['high']} high, "
            f"{al['medium']} medium, {al['low']} low. Agents {ah['online']}/{ah['total_agents']} online.",
            styles["body"]))
        if al.get("critical_events"):
            rows = [["Device", "Alert", "Severity", "Time"]]
            for e in al["critical_events"][:40]:
                rows.append([e.get("device") or "—", (e.get("alert") or "")[:40],
                             e.get("severity", ""), (e.get("time") or "")[11:19]])
            flow.append(_dtable(rows, col_widths=[140, 200, 70, 70], header_bg=ERROR))

    # spane access — only if failures, after-hours, or admin actions.
    if sp.get("total_failures") or sp.get("after_hours_logins") or sp.get("admin_actions"):
        flow.append(PageBreak())
        _section(flow, styles, "8 · spane Access Events")
        flow.append(Paragraph(
            f"{sp.get('total_failures', 0)} failed login(s); "
            f"{len(sp.get('after_hours_logins', []))} after-hours; "
            f"{len(sp.get('new_source_ips', []))} new source IP(s); "
            f"{len(sp.get('admin_actions', []))} admin action(s).", styles["body"]))
        if sp.get("login_failures"):
            rows = [["Failed login", "User", "Source IP"]]
            rows += [[(e["time"] or "")[11:19], e["username"], e["source_ip"] or ""]
                     for e in sp["login_failures"][:20]]
            flow.append(_dtable(rows, col_widths=[110, 160, 130], header_bg=ERROR))
        if sp.get("after_hours_logins"):
            flow.append(Spacer(1, 4))
            rows = [["After-hours login", "User", "Source IP"]]
            rows += [[(e["time"] or "")[11:19], e["username"], e["source_ip"] or ""]
                     for e in sp["after_hours_logins"][:20]]
            flow.append(_dtable(rows, col_widths=[110, 160, 130], header_bg=WARNING))
        if sp.get("admin_actions"):
            flow.append(Spacer(1, 4))
            rows = [["Admin action", "User", "Action", "Target"]]
            rows += [[(e["time"] or "")[11:19], e["username"], e["event_type"],
                      (e.get("target") or "")[:28]] for e in sp["admin_actions"][:20]]
            flow.append(_dtable(rows, col_widths=[90, 120, 130, 110]))

    header_label = f"{data.get('period_label', 'Daily')} Ops — {data.get('date_label', rdate)}"
    deco = _page_decorator(header_label, gen)
    doc.build(flow, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()


def daily_ops_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = _SafeCsvWriter(out)
    w.writerow(["section", "key", "value"])
    sec = data["security_events"]
    w.writerow(["device_security", "total_failures", sec["total_failures"]])
    w.writerow(["device_security", "unique_sources", sec["unique_sources"]])
    w.writerow(["device_security", "device_count", sec.get("device_count", 0)])
    w.writerow(["device_security", "flags", len(sec.get("flags", []))])
    w.writerow(["device_security", "success_after_failures", len(sec.get("success_after_failures", []))])
    sp = data.get("spane_access_events", {})
    w.writerow(["spane_access", "total_failures", sp.get("total_failures", 0)])
    w.writerow(["spane_access", "after_hours_logins", len(sp.get("after_hours_logins", []))])
    w.writerow(["spane_access", "admin_actions", len(sp.get("admin_actions", []))])
    ce = data["compliance_events"]
    w.writerow(["compliance", "fleet_avg_today", ce.get("fleet_avg_today")])
    w.writerow(["compliance", "fleet_avg_prev", ce.get("fleet_avg_prev")])
    w.writerow(["compliance", "total_failing_devices", ce.get("total_failing_devices", 0)])
    svc = data.get("service_checks", {})
    w.writerow(["service_checks", "total_executions", svc.get("total_executions", 0)])
    w.writerow(["service_checks", "total_failures", svc.get("total_failures", 0)])
    w.writerow(["service_checks", "pass_rate", svc.get("pass_rate")])
    w.writerow(["service_checks", "affected_checks", svc.get("affected_checks", 0)])
    av = data["device_availability"]
    w.writerow(["availability", "availability_pct", av["availability_pct"]])
    w.writerow(["availability", "total_outages", av["total_outages"]])
    w.writerow(["availability", "total_downtime_minutes", av["total_downtime_minutes"]])
    ch = data["collection_health"]
    w.writerow(["collection", "successful", ch["successful"]])
    w.writerow(["collection", "failed", ch["failed"]])
    w.writerow(["collection", "success_rate", ch.get("success_rate", 0)])
    for s in ch.get("by_status", []):
        w.writerow(["collection_status", s["status"], s["count"]])
    w.writerow([])
    w.writerow(["service_check", "device", "type", "failures", "avg_duration_ms", "correlated_outage"])
    for s in svc.get("summaries", []):
        w.writerow([s["check_name"], s["device"], s["check_type"], s["failure_count"],
                    s.get("avg_duration_ms"), "yes" if s.get("correlated_outage") else ""])
    w.writerow([])
    w.writerow(["config_change_device", "detected_at", "lines_added", "lines_removed"])
    for c in data["config_changes"]:
        w.writerow([c["hostname"], c["detected_at"], c["lines_added"], c["lines_removed"]])
    return out.getvalue().encode("utf-8")


def daily_ops_html(data: dict) -> bytes:
    sec = data["security_events"]
    av = data["device_availability"]
    ch = data["collection_health"]
    ah = data["agent_health"]
    al = data["alerts_summary"]
    sp = data.get("spane_access_events", {})
    import html as _html

    def _diff_html(diff_text):
        out = []
        for ln in (diff_text or "").splitlines():
            esc = _html.escape(ln) or "&nbsp;"
            if ln.startswith("+") and not ln.startswith("+++"):
                out.append(f'<div style="background:#dcfce7;color:#166534">{esc}</div>')
            elif ln.startswith("-") and not ln.startswith("---"):
                out.append(f'<div style="background:#fee2e2;color:#991b1b">{esc}</div>')
            elif ln.startswith("@@"):
                out.append(f'<div style="color:#2563eb">{esc}</div>')
            else:
                out.append(f'<div style="color:#6b7280">{esc}</div>')
        return "".join(out)

    # Device security: grouped failures + flags + success-after-failures.
    sec_groups = ""
    if sec.get("groups"):
        grows = "".join(
            f"<tr><td>{_html.escape(g.get('username') or '(unknown)')}</td>"
            f"<td>{_html.escape(', '.join(g.get('source_ips') or []) or '—')}"
            f"{(' · ' + str(g['device_count']) + ' devices') if g.get('device_count', 0) > 1 else ''}</td>"
            f"<td>{g['count']}</td><td>{g.get('time_range', '')}</td></tr>"
            for g in sec["groups"][:20])
        sec_groups = ("<table><tr><th>Username</th><th>Source / Devices</th><th>Count</th>"
                      f"<th>Time</th></tr>{grows}</table>")
    sec_flags = "".join(
        f"<p style='color:#b45309'>{_html.escape(f)}</p>" for f in sec.get("flags", [])[:10])
    for s in (sec.get("success_after_failures") or [])[:10]:
        sec_flags += (f"<p style='color:#991b1b'>⚠️ SUCCESS AFTER FAILURES: "
                      f"{_html.escape(s['username'])} failed {s['fail_count']} time(s) then "
                      f"succeeded at {s.get('at', '')} on {_html.escape(s.get('device') or 'unknown')} "
                      f"— review immediately.</p>")

    # Compliance status line + failing table.
    ce = data["compliance_events"]
    if ce.get("fleet_avg_today") is not None:
        delta = ce.get("fleet_avg_delta")
        trend = ""
        if delta is not None and ce.get("fleet_avg_prev") is not None:
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            trend = f" {arrow} from {ce['fleet_avg_prev']} yesterday"
        compliance_line = (f"Fleet score: {ce['fleet_avg_today']}/100 ({ce.get('fleet_grade') or '—'})"
                           f"{trend}. {ce['total_failing_devices']} device(s) currently failing; "
                           f"{ce.get('unsaved_configs', 0)} with unsaved configs.")
    else:
        compliance_line = (f"No stored compliance scores for this period. "
                           f"{ce.get('unsaved_configs', 0)} device(s) with unsaved configs.")
    failing_table = ""
    if ce.get("failing_devices"):
        frows = "".join(
            f"<tr><td>{_html.escape(d['hostname'])}</td><td>{d['score']}</td>"
            f"<td>{d['grade']}</td><td>{_html.escape(d.get('site') or '')}</td></tr>"
            for d in ce["failing_devices"])
        failing_table = ("<table><tr><th>Device</th><th>Score</th><th>Grade</th><th>Site</th></tr>"
                         f"{frows}</table>")

    # Service check failures.
    svc = data.get("service_checks", {})
    if not svc.get("configured"):
        svc_block = f"<p>{_html.escape(svc.get('note') or 'No service checks configured.')}</p>"
    else:
        srows = "".join(
            f"<tr><td>{_html.escape(s['check_name'])}</td><td>{_html.escape(s['device'] or '')}</td>"
            f"<td>{s['check_type']}</td><td>{s['failure_count']}</td>"
            f"<td>{(str(s['avg_duration_s']) + 's') if s.get('avg_duration_s') is not None else '—'}</td>"
            f"<td>{(s['first_failure'] or '')[11:16]}–{(s['last_failure'] or '')[11:16]}</td>"
            f"<td>{'⟲ outage' if s.get('correlated_outage') else ''}</td></tr>"
            for s in svc.get("summaries", []))
        svc_block = (
            f"<p>{svc['total_executions']} executions: {svc['total_passing']} passed"
            f"{f' ({svc['pass_rate']}%)' if svc.get('pass_rate') is not None else ''}, "
            f"{svc['total_failures']} failed across {svc['affected_checks']} check(s).</p>"
            + ("<table><tr><th>Check</th><th>Device</th><th>Type</th><th>Fails</th>"
               f"<th>Avg Dur</th><th>Window</th><th>Note</th></tr>{srows}</table>" if srows else ""))

    # Collection status breakdown.
    coll_table = ""
    if ch.get("by_status"):
        crows = "".join(f"<tr><td>{s['status']}</td><td>{s['count']}</td><td>{s['rate']}%</td></tr>"
                        for s in ch["by_status"])
        coll_table = f"<table><tr><th>Status</th><th>Count</th><th>Rate</th></tr>{crows}</table>"

    rows = "".join(
        f"<tr><td>{c['hostname']}</td><td>{c['detected_at'][11:19]}</td>"
        f"<td>+{c['lines_added']}/-{c['lines_removed']}</td><td>{c['diff_summary']}</td></tr>"
        for c in data["config_changes"])
    diff_blocks = "".join(
        f"<h3 style='margin-bottom:2px'>{c['hostname']} — {c['detected_at'][11:19]}"
        f"{(' · prev backup ' + c['previous_backup_at'][11:19]) if c.get('previous_backup_at') else ''}</h3>"
        f"<pre style='font-family:Courier,monospace;font-size:12px;border:1px solid #d1d5db;"
        f"border-radius:4px;overflow-x:auto;margin:0 0 12px'>{_diff_html(c.get('diff'))}</pre>"
        for c in data["config_changes"])
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>spane Daily Operations — {data['report_date']}</title>
<style>body{{font-family:system-ui,sans-serif;color:#1f2937;margin:2rem}}
h1{{color:#2563eb}}h2{{border-bottom:2px solid #2563eb;padding-bottom:.2rem}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d1d5db;padding:4px 8px;text-align:left;font-size:14px}}
tr:nth-child(even){{background:#f3f4f6}}</style></head><body>
<p style="color:#6b7280">spane — unified infrastructure visibility</p>
<h1>Daily Operations Report</h1><p>{data['report_date']} — All Sites</p>
<h2>Device Security Events</h2><p>{sec['total_failures']} authentication failures from {sec['unique_sources']} sources
across {sec.get('device_count', 0)} devices.{(' ' + sec['note']) if sec.get('note') else ''}</p>
{sec_groups}{sec_flags}
<h2>Compliance Status</h2><p>{compliance_line}</p>{failing_table}
<h2>Availability</h2><p>Fleet availability {av['availability_pct']}% · {av['total_outages']} outages
({av['total_downtime_minutes']} min downtime).</p>
<h2>Service Check Failures</h2>{svc_block}
<h2>Collection Health</h2><p>{ch['total_attempts']} attempts across {ch.get('device_count', 0)} devices — {ch.get('success_rate', 0)}% success.</p>{coll_table}
<h2>Config Changes</h2><table><tr><th>Device</th><th>Time</th><th>+/-</th><th>Summary</th></tr>{rows or '<tr><td colspan=4>None</td></tr>'}</table>
{diff_blocks}
<h2>Agents &amp; Alerts</h2><p>Agents {ah['online']}/{ah['total_agents']} online ·
Alerts {al['total']} ({al['critical']} crit, {al['high']} high).</p>
<h2>spane Access Events</h2><p>{sp.get('total_failures', 0)} failed logins;
{len(sp.get('after_hours_logins', []))} after-hours; {len(sp.get('new_source_ips', []))} new IPs;
{len(sp.get('admin_actions', []))} admin actions.</p>
<p style="color:#6b7280;font-size:12px">Generated by spane · {data['generated_at'][:19].replace('T',' ')} UTC</p>
</body></html>"""
    return html.encode("utf-8")


def to_json(data: dict) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")
