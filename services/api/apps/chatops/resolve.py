"""
ChatOps intent resolution — Phase 3.

``resolve(intent, params)`` gathers the real data for an intent and returns a
**structured** :class:`IntentResult` (title + labelled fields + free-form lines +
a severity). It is deliberately format-agnostic: the per-platform renderers in
``apps.chatops.format`` turn an IntentResult into Slack Block Kit / Teams
Adaptive Card / Google Chat cardsV2 / Discord embed / Mattermost markdown, and
every formatter also carries ``IntentResult.plain()`` as a text fallback.

Security: responses never include credentials or internal management IPs — only
hostnames, status, coarse metrics, CVE/risk/lifecycle summaries. Every resolver
degrades gracefully (device not found, no telemetry, subsystem down) to a plain
message; nothing here raises out to the webhook handler.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import timedelta

logger = logging.getLogger(__name__)

# The closed set of intents the parser/NLP may produce. Kept here so nlp.py and
# the tests share one source of truth.
KNOWN_INTENTS = frozenset({
    "device_status", "site_status", "active_alerts", "cve_query", "eol_query", "help",
})

_HELP_LINES = [
    "`status of <device>` — device status, health, CVEs, risk, EOL",
    "`status of site <site>` — site rollup",
    "`any alerts` — active alerts right now",
    "`CVEs affecting <device>` — unpatched CVEs for a device",
    "`EOL for <device>` — lifecycle / end-of-life dates",
    "`help` — this message",
]

# CVE severities, worst first, for ordered grouping/summaries.
_SEV_ORDER = ("critical", "high", "medium", "low", "none")


@dataclass
class IntentResult:
    """Structured, format-agnostic result of resolving one ChatOps intent."""
    title: str = ""
    fields: list = field(default_factory=list)   # list[tuple[label, value]]
    lines: list = field(default_factory=list)    # list[str] — free-form body
    severity: str = "info"                        # info|low|medium|high|critical

    def plain(self) -> str:
        """Markdown-neutral plain-text fallback (NO ``*`` / backticks).

        Used verbatim by the Teams/Google-Chat/Discord/Mattermost fallback field
        and the Slack ``text`` summary, so it must not leak Slack-style ``*bold*``
        markup that other clients would render as literal asterisks.
        """
        out = []
        if self.title:
            out.append(self.title)
        for label, value in self.fields:
            out.append(f"{label}: {value}")
        out.extend(self.lines)
        return "\n".join(out) if out else "No data."


# ── device resolution ─────────────────────────────────────────────────────────

def _find_device(name: str):
    """Resolve a device by hostname (icontains), then IP only when the term
    parses as an IP (``ip_address`` is an INET column — a non-IP string errors)."""
    from apps.devices.models import Device
    if not name:
        return None
    d = Device.objects.select_related("site", "role").filter(
        hostname__icontains=name).first()
    if d:
        return d
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return None
    return Device.objects.select_related("site", "role").filter(ip_address=name).first()


def _unpatched_cve_counts(device) -> dict:
    """{severity: count} of this device's UNPATCHED CVEs (only non-zero buckets)."""
    from django.db.models import Count
    rows = (device.cves.filter(is_patched=False)
            .values("cve__severity").annotate(n=Count("id")))
    return {r["cve__severity"]: r["n"] for r in rows if r["n"]}


def _cve_summary(counts: dict) -> str:
    """'2 critical, 5 high' worst-first, or 'none'."""
    parts = [f"{counts[s]} {s}" for s in _SEV_ORDER if counts.get(s)]
    return ", ".join(parts) if parts else "none"


def _worst_cve_severity(counts: dict) -> str:
    for s in _SEV_ORDER:
        if counts.get(s):
            return s
    return "info"


def _risk_score(device):
    """Device composite risk score (float) or None when not scored."""
    try:
        return float(device.risk_score.score)
    except Exception:  # RelatedObjectDoesNotExist / None
        return None


def _eol_flags(device):
    """Return (passed, upcoming) lists of (label, date) lifecycle milestones.

    ``passed`` = milestone_date already in the past; ``upcoming`` = within 90 days.
    """
    from django.utils import timezone
    today = timezone.now().date()
    soon = today + timedelta(days=90)
    passed, upcoming = [], []
    for m in device.lifecycle_milestones.all():
        label = m.get_milestone_type_display()
        if m.milestone_date <= today:
            passed.append((label, m.milestone_date))
        elif m.milestone_date <= soon:
            upcoming.append((label, m.milestone_date))
    return passed, upcoming


# ── resolvers ─────────────────────────────────────────────────────────────────

def _resolve_device_status(params) -> IntentResult:
    name = (params.get("name") or "").strip()
    device = _find_device(name)
    if not device:
        return IntentResult(title=f"Device '{name}' not found.")

    fields = [("Status", device.status)]
    fields.append(("Reachability", "reachable" if device.is_reachable else "unreachable"))
    if not device.is_reachable and device.unreachable_since:
        fields.append(("Down since", device.unreachable_since.strftime("%Y-%m-%d %H:%M UTC")))
    if device.vendor:
        fields.append(("Vendor", device.vendor))
    if device.model:
        fields.append(("Model", device.model))
    if device.os_version:
        fields.append(("OS", device.os_version))
    if device.role_id:
        fields.append(("Role", device.role.name))
    if device.site_id:
        fields.append(("Site", device.site.name))

    # CPU/memory — omit entirely when InfluxDB has no data (never error).
    try:
        from apps.devices.metrics_influx import query_device_metrics
        metrics = (query_device_metrics(str(device.id)) or {}).get("metrics", {}) or {}
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
        logger.warning("chatops metrics lookup failed for %s: %s", device.id, exc)
        metrics = {}
    cpu = metrics.get("cpu_pct")
    mem = metrics.get("memory_used_pct")
    if isinstance(cpu, (int, float)):
        fields.append(("CPU", f"{round(cpu)}%"))
    if isinstance(mem, (int, float)):
        fields.append(("Memory", f"{round(mem)}%"))

    # Unpatched CVEs by severity.
    cve_counts = _unpatched_cve_counts(device)
    fields.append(("CVEs (unpatched)", _cve_summary(cve_counts)))

    # Risk score.
    risk = _risk_score(device)
    if risk is not None:
        fields.append(("Risk score", f"{round(risk)}/100"))

    # EOL flag.
    passed, upcoming = _eol_flags(device)
    lines = []
    if passed:
        worst = ", ".join(f"{lbl} {d.isoformat()}" for lbl, d in passed)
        lines.append(f"⚠ Past lifecycle: {worst}")
    elif upcoming:
        nxt = ", ".join(f"{lbl} {d.isoformat()}" for lbl, d in upcoming)
        lines.append(f"Approaching: {nxt}")

    # Severity: unreachable or a critical CVE → loud; otherwise from CVE worst.
    if not device.is_reachable:
        severity = "high"
    else:
        severity = _worst_cve_severity(cve_counts)
        if severity == "info" and passed:
            severity = "medium"

    return IntentResult(title=device.hostname, fields=fields, lines=lines, severity=severity)


def _resolve_site_status(params) -> IntentResult:
    from apps.alerts.models import AlertEvent
    from apps.devices.models import Device, Site
    name = (params.get("name") or "").strip()
    site = Site.objects.filter(name__icontains=name).first()
    if not site:
        return IntentResult(title=f"Site '{name}' not found.")

    total = Device.objects.filter(site=site).count()
    active = Device.objects.filter(site=site, status="active").count()
    # Alerts carry the device as labels["device_id"] (string) — no FK to join on.
    site_ids = [str(i) for i in Device.objects.filter(site=site).values_list("id", flat=True)]
    firing = (AlertEvent.objects.filter(state="firing", labels__device_id__in=site_ids).count()
              if site_ids else 0)
    worst = (Device.objects.filter(site=site, risk_score__isnull=False)
             .order_by("-risk_score__score").first())
    worst_risk = _risk_score(worst) if worst else None

    fields = [
        ("Devices active", f"{active}/{total}"),
        ("Firing alerts", str(firing)),
    ]
    if worst_risk is not None:
        fields.append(("Worst risk", f"{round(worst_risk)}/100 ({worst.hostname})"))
    severity = "high" if firing else "info"
    return IntentResult(title=f"Site {site.name}", fields=fields, severity=severity)


def _resolve_active_alerts(_params) -> IntentResult:
    from apps.alerts.models import AlertEvent
    count = AlertEvent.objects.filter(state="firing").count()
    if count == 0:
        return IntentResult(title="No active alerts.", severity="info")
    recent = (AlertEvent.objects.filter(state="firing")
              .select_related("rule").order_by("-created_at")[:5])
    lines = [f"[{e.rule.severity.upper()}] {e.rule.name}" for e in recent]
    severities = {(e.rule.severity or "").lower() for e in recent}
    severity = next((s for s in _SEV_ORDER if s in severities), "info")
    extra = f" (showing 5 of {count})" if count > 5 else ""
    return IntentResult(title=f"{count} active alert(s){extra}", lines=lines, severity=severity)


def _resolve_cve_query(params) -> IntentResult:
    name = (params.get("name") or "").strip()
    device = _find_device(name)
    if not device:
        return IntentResult(title=f"Device '{name}' not found.")
    qs = (device.cves.filter(is_patched=False)
          .select_related("cve").order_by("cve__severity"))
    counts = _unpatched_cve_counts(device)
    total = sum(counts.values())
    if total == 0:
        return IntentResult(title=f"{device.hostname}: no unpatched CVEs.", severity="info")

    # Cap the listed CVEs; worst-first by severity order.
    cap = 10
    ordered = sorted(qs, key=lambda dc: _SEV_ORDER.index(dc.cve.severity)
                     if dc.cve.severity in _SEV_ORDER else len(_SEV_ORDER))
    lines = [f"[{dc.cve.severity.upper()}] {dc.cve.cve_id}" for dc in ordered[:cap]]
    if total > cap:
        lines.append(f"…and {total - cap} more — see the Security tab.")
    return IntentResult(
        title=f"{device.hostname}: {total} unpatched CVE(s) — {_cve_summary(counts)}",
        lines=lines, severity=_worst_cve_severity(counts))


def _resolve_eol_query(params) -> IntentResult:
    name = (params.get("name") or "").strip()
    device = _find_device(name)
    if not device:
        return IntentResult(title=f"Device '{name}' not found.")
    from django.utils import timezone
    today = timezone.now().date()
    milestones = list(device.lifecycle_milestones.order_by("milestone_date"))
    if not milestones:
        return IntentResult(title=f"{device.hostname}: no lifecycle data.", severity="info")
    lines = []
    any_passed = False
    for m in milestones:
        passed = m.milestone_date <= today
        any_passed = any_passed or passed
        flag = " — PASSED" if passed else ""
        lines.append(f"{m.get_milestone_type_display()}: {m.milestone_date.isoformat()}{flag}")
    return IntentResult(title=f"{device.hostname}: lifecycle", lines=lines,
                        severity="medium" if any_passed else "info")


def _resolve_help(_params) -> IntentResult:
    return IntentResult(title="spane commands", lines=list(_HELP_LINES), severity="info")


_RESOLVERS = {
    "device_status": _resolve_device_status,
    "site_status": _resolve_site_status,
    "active_alerts": _resolve_active_alerts,
    "cve_query": _resolve_cve_query,
    "eol_query": _resolve_eol_query,
    "help": _resolve_help,
}


def resolve(intent: str, params: dict | None = None) -> IntentResult:
    """Resolve ``intent`` to a structured :class:`IntentResult`.

    Unknown intents and any resolver error degrade to the help result — a
    ChatOps query must never 500 the webhook.
    """
    params = params or {}
    fn = _RESOLVERS.get(intent)
    if fn is None:
        return _resolve_help(params)
    try:
        return fn(params)
    except Exception as exc:  # noqa: BLE001 — never raise into the webhook handler
        logger.error("chatops resolve error for intent=%s: %s", intent, exc)
        return IntentResult(title="Sorry — that query couldn't be completed.", severity="info")
