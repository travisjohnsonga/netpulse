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
    flow.append(Paragraph("1 · Device Security Events", styles["h2"]))
    flow.append(Paragraph(
        f"{sec['total_failures']} authentication failure(s) from "
        f"{sec['unique_sources']} unique source(s) across {sec.get('device_count', 0)} device(s).",
        styles["body"]))
    if sec.get("groups"):
        rows = [["Username", "Source IP / Devices", "Count", "Time"]]
        for g in sec["groups"][:20]:
            srcs = ", ".join(g.get("source_ips") or []) or "—"
            if g.get("device_count", 0) > 1:
                srcs = f"{srcs} · {g['device_count']} devices"
            rows.append([g.get("username") or "(unknown)", srcs[:40], g["count"],
                         g.get("time_range", "")])
        flow.append(_table(rows, col_widths=[130, 200, 50, 80]))
    elif sec.get("note"):
        flow.append(Paragraph(sec["note"], styles["small"]))
    for fl in sec.get("flags", [])[:10]:
        flow.append(Paragraph(fl, styles["small"]))
    saf = sec.get("success_after_failures") or []
    if saf:
        for s in saf[:10]:
            flow.append(Paragraph(
                f"⚠️ SUCCESS AFTER FAILURES: {s['username']} failed {s['fail_count']} time(s) "
                f"then succeeded at {s.get('at', '')} on {s.get('device') or 'unknown'} — "
                f"review immediately.", styles["small"]))
    elif sec.get("total_failures"):
        flow.append(Paragraph("No successes-after-failures detected.", styles["small"]))

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
    flow.append(Paragraph("3 · Compliance Status", styles["h2"]))
    if ce.get("fleet_avg_today") is not None:
        trend = ""
        if ce.get("fleet_avg_delta") is not None and ce.get("fleet_avg_prev") is not None:
            arrow = "↑" if ce["fleet_avg_delta"] > 0 else ("↓" if ce["fleet_avg_delta"] < 0 else "→")
            trend = f" {arrow} from {ce['fleet_avg_prev']} yesterday"
        flow.append(Paragraph(
            f"Fleet score: {ce['fleet_avg_today']}/100 ({ce.get('fleet_grade') or '—'}){trend}. "
            f"{ce['total_failing_devices']} device(s) currently failing; "
            f"{ce.get('unsaved_configs', 0)} with unsaved configs.", styles["body"]))
    else:
        flow.append(Paragraph(
            f"No stored compliance scores for this period. "
            f"{ce.get('unsaved_configs', 0)} device(s) with unsaved configs.", styles["body"]))
    if ce.get("failing_devices"):
        rows = [["Device", "Score", "Grade", "Site"]]
        rows += [[d["hostname"], d["score"], d["grade"], d.get("site") or ""]
                 for d in ce["failing_devices"]]
        flow.append(_table(rows, col_widths=[200, 60, 50, 100], header_bg=RED))
    if ce.get("degraded") or ce.get("improved"):
        flow.append(Spacer(1, 4))
        flow.append(Paragraph("Score changes from previous day:", styles["small"]))
        for d in ce.get("degraded", [])[:10]:
            flow.append(Paragraph(
                f"↓ {d['hostname']} ({d['score_prev']}→{d['score_today']})", styles["small"]))
        for d in ce.get("improved", [])[:10]:
            flow.append(Paragraph(
                f"↑ {d['hostname']} ({d['score_prev']}→{d['score_today']})", styles["small"]))

    svc = data.get("service_checks", {})
    flow.append(Paragraph("4 · Service Check Failures", styles["h2"]))
    if not svc.get("configured"):
        flow.append(Paragraph(svc.get("note") or "No service checks configured.", styles["small"]))
    else:
        flow.append(Paragraph(
            f"{svc['total_executions']} check execution(s): {svc['total_passing']} passed"
            f"{f' ({svc['pass_rate']}%)' if svc.get('pass_rate') is not None else ''}, "
            f"{svc['total_failures']} failed across {svc['affected_checks']} check(s).",
            styles["body"]))
        if svc.get("summaries"):
            rows = [["Check", "Device", "Type", "Fails", "Avg Dur", "Window"]]
            for s in svc["summaries"][:25]:
                dur = f"{s['avg_duration_s']}s" if s.get("avg_duration_s") is not None else "—"
                win = f"{(s['first_failure'] or '')[11:16]}–{(s['last_failure'] or '')[11:16]}"
                rows.append([s["check_name"][:24], (s["device"] or "")[:16], s["check_type"],
                             s["failure_count"], dur, win])
            flow.append(_table(rows, col_widths=[140, 110, 50, 45, 60, 95], header_bg=RED))
            for s in svc["summaries"][:25]:
                if s.get("correlated_outage"):
                    co = s["correlated_outage"]
                    flow.append(Paragraph(
                        f"⟲ Correlated with device outage: {co['hostname']} was unreachable "
                        f"{(co['down_at'] or '')[11:16]}–{(co.get('recovered_at') or 'now')[11:16]} "
                        f"(matches {s['check_name']} failures).", styles["small"]))

    cc = data["config_changes"]
    flow.append(Paragraph("5 · Configuration Changes", styles["h2"]))
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
    flow.append(Paragraph("6 · Collection Health", styles["h2"]))
    flow.append(Paragraph(
        f"{ch['total_attempts']} attempt(s) across {ch.get('device_count', 0)} device(s) — "
        f"{ch.get('success_rate', 0)}% success.", styles["body"]))
    if ch.get("by_status"):
        rows = [["Status", "Count", "Rate"]]
        rows += [[s["status"], s["count"], f"{s['rate']}%"] for s in ch["by_status"]]
        flow.append(_table(rows, col_widths=[150, 80, 80]))
    if ch["failed_devices"]:
        flow.append(Spacer(1, 4))
        rows = [["Device", "Error", "Attempts"]]
        rows += [[f["hostname"], f["error"], f["attempts"]] for f in ch["failed_devices"]]
        flow.append(_table(rows, header_bg=RED))

    ah = data["agent_health"]
    al = data["alerts_summary"]
    flow.append(Paragraph("7 · Agent Health & Alerts", styles["h2"]))
    flow.append(Paragraph(
        f"Agents: {ah['online']}/{ah['total_agents']} online. "
        f"Alerts: {al['total']} ({al['critical']} crit, {al['high']} high, "
        f"{al['medium']} med, {al['low']} low).", styles["body"]))
    flow.append(Spacer(1, 4))

    sp = data.get("spane_access_events", {})
    flow.append(Paragraph("8 · spane Access Events", styles["h2"]))
    flow.append(Paragraph(
        f"{sp.get('total_failures', 0)} failed login(s); "
        f"{len(sp.get('after_hours_logins', []))} after-hours login(s); "
        f"{len(sp.get('new_source_ips', []))} new source IP(s); "
        f"{len(sp.get('admin_actions', []))} admin action(s).", styles["body"]))
    if sp.get("login_failures"):
        rows = [["Failed login", "User", "Source IP"]]
        rows += [[(e["time"] or "")[11:19], e["username"], e["source_ip"] or ""]
                 for e in sp["login_failures"][:20]]
        flow.append(_table(rows, col_widths=[100, 120, 120], header_bg=RED))
    if sp.get("after_hours_logins"):
        flow.append(Spacer(1, 4))
        rows = [["After-hours login", "User", "Source IP"]]
        rows += [[(e["time"] or "")[11:19], e["username"], e["source_ip"] or ""]
                 for e in sp["after_hours_logins"][:20]]
        flow.append(_table(rows, col_widths=[100, 120, 120]))
    if sp.get("admin_actions"):
        flow.append(Spacer(1, 4))
        rows = [["Admin action", "User", "Action", "Target"]]
        rows += [[(e["time"] or "")[11:19], e["username"], e["event_type"],
                  (e.get("target") or "")[:30]] for e in sp["admin_actions"][:20]]
        flow.append(_table(rows, col_widths=[80, 110, 130, 110]))

    doc.build(flow, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


def daily_ops_csv(data: dict) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
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
