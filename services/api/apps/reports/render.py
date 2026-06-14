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


def compliance_summary_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
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
def daily_ops_pdf(data: dict) -> bytes:
    from reportlab.platypus import Paragraph, Spacer
    styles = _styles()
    buf, doc = _doc("spane Daily Operations")
    flow = []
    _header(flow, styles, "Daily Operations Report", f"{data['report_date']} — All Sites")

    sec = data["security_events"]
    flow.append(Paragraph("1 · Security Events", styles["h2"]))
    flow.append(Paragraph(
        f"{sec['total_failures']} login failure(s) from {sec['unique_sources']} source(s); "
        f"{len(sec['after_hours_logins'])} after-hours login(s); "
        f"{len(sec['new_source_ips'])} new source IP(s).", styles["body"]))
    if sec["login_failures"]:
        rows = [["Time", "User", "Source IP"]]
        rows += [[(e["time"] or "")[11:19], e["username"], e["source_ip"] or ""] for e in sec["login_failures"][:20]]
        flow.append(_table(rows, col_widths=[80, 120, 120]))

    av = data["device_availability"]
    flow.append(Paragraph("2 · Device Availability", styles["h2"]))
    flow.append(Paragraph(
        f"Fleet availability {av['availability_pct']}% · {av['total_outages']} outage(s), "
        f"{av['total_downtime_minutes']} min total downtime.", styles["body"]))
    if av["went_down"]:
        rows = [["Device", "Down at", "Duration (min)", "Site"]]
        rows += [[o["hostname"], (o["down_at"] or "")[11:19], o["duration_minutes"], o["site"] or ""]
                 for o in av["went_down"]]
        flow.append(_table(rows))

    ce = data["compliance_events"]
    flow.append(Paragraph("3 · Compliance Events", styles["h2"]))
    flow.append(Paragraph(
        f"{len(ce['new_failures'])} new failure(s), {len(ce['resolved'])} resolved; "
        f"{ce['total_failing_devices']} device(s) currently failing.", styles["body"]))

    cc = data["config_changes"]
    flow.append(Paragraph("4 · Configuration Changes", styles["h2"]))
    if not cc:
        flow.append(Paragraph("No config changes detected.", styles["small"]))
    else:
        # Summary table first.
        rows = [["Device", "+", "-", "Detected"]]
        rows += [[c["hostname"], f"+{c['lines_added']}", f"-{c['lines_removed']}",
                  (c["detected_at"] or "")[11:19]] for c in cc]
        flow.append(_table(rows, col_widths=[210, 50, 50, 90]))
        flow.append(Spacer(1, 8))
        # Then a colour-coded diff per device.
        for c in cc:
            prev = (c.get("previous_backup_at") or "")[11:19]
            head = f"{c['hostname']} — config change at {(c['detected_at'] or '')[11:19]}"
            if prev:
                head += f"  ·  previous backup {prev}"
            flow.append(Paragraph(head, styles["small"]))
            tbl = _diff_table(c.get("diff") or "")
            flow.append(tbl if tbl is not None else Paragraph("(no diff available)", styles["small"]))
            flow.append(Spacer(1, 8))

    ch = data["collection_health"]
    flow.append(Paragraph("5 · Collection Health", styles["h2"]))
    flow.append(Paragraph(
        f"{ch['successful']}/{ch['total_attempts']} attempts succeeded; {ch['failed']} failed.", styles["body"]))
    if ch["failed_devices"]:
        rows = [["Device", "Error", "Attempts"]]
        rows += [[f["hostname"], f["error"], f["attempts"]] for f in ch["failed_devices"]]
        flow.append(_table(rows, header_bg=RED))

    ah = data["agent_health"]
    al = data["alerts_summary"]
    flow.append(Paragraph("6 · Agent Health & Alerts", styles["h2"]))
    flow.append(Paragraph(
        f"Agents: {ah['online']}/{ah['total_agents']} online. "
        f"Alerts: {al['total']} ({al['critical']} crit, {al['high']} high, "
        f"{al['medium']} med, {al['low']} low).", styles["body"]))
    flow.append(Spacer(1, 4))

    doc.build(flow, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


def daily_ops_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["section", "key", "value"])
    sec = data["security_events"]
    w.writerow(["security", "total_failures", sec["total_failures"]])
    w.writerow(["security", "unique_sources", sec["unique_sources"]])
    w.writerow(["security", "after_hours_logins", len(sec["after_hours_logins"])])
    av = data["device_availability"]
    w.writerow(["availability", "availability_pct", av["availability_pct"]])
    w.writerow(["availability", "total_outages", av["total_outages"]])
    w.writerow(["availability", "total_downtime_minutes", av["total_downtime_minutes"]])
    ch = data["collection_health"]
    w.writerow(["collection", "successful", ch["successful"]])
    w.writerow(["collection", "failed", ch["failed"]])
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
<h2>Security</h2><p>{sec['total_failures']} login failures from {sec['unique_sources']} sources;
{len(sec['after_hours_logins'])} after-hours logins; {len(sec['new_source_ips'])} new IPs.</p>
<h2>Availability</h2><p>Fleet availability {av['availability_pct']}% · {av['total_outages']} outages
({av['total_downtime_minutes']} min downtime).</p>
<h2>Collection Health</h2><p>{ch['successful']}/{ch['total_attempts']} succeeded; {ch['failed']} failed.</p>
<h2>Config Changes</h2><table><tr><th>Device</th><th>Time</th><th>+/-</th><th>Summary</th></tr>{rows or '<tr><td colspan=4>None</td></tr>'}</table>
{diff_blocks}
<h2>Agents &amp; Alerts</h2><p>Agents {ah['online']}/{ah['total_agents']} online ·
Alerts {al['total']} ({al['critical']} crit, {al['high']} high).</p>
<p style="color:#6b7280;font-size:12px">Generated by spane · {data['generated_at'][:19].replace('T',' ')} UTC</p>
</body></html>"""
    return html.encode("utf-8")


def to_json(data: dict) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")
