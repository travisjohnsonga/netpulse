"""
LLDP-aware interface compliance engine.

A rule selects switch interfaces by a trigger and runs config ``checks`` against
each matching interface's config block:

- ``lldp_capability``      — neighbour advertises this LLDP capability (the
  trigger value is normalised through apps.devices.lldp, so ``wlan-access-point``
  matches the stored ``wlan-ap`` token and catches every AP vendor).
- ``lldp_neighbor_platform`` — neighbour resolves to a Device of one of these
  platforms (comma-separated).
- ``lldp_neighbor_role``   — neighbour's matched Device has one of these roles.
- ``interface_description``— interface's ``description`` line matches a regex.
- ``manual``               — explicit ``hostname:interface`` pairs.

Interface config comes from the latest config backup (DeviceConfig); the block
is matched by interface name, canonicalised so ``Gi1/0/5`` and
``GigabitEthernet1/0/5`` resolve to the same stanza.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# ── interface-config extraction ──────────────────────────────────────────────
def _iter_interface_blocks(content: str):
    """Yield ``(header_name, block_text)`` for each ``interface <name>`` stanza.

    A block runs from its ``interface`` header to the next non-indented line
    (the next top-level stanza). Works for IOS/NX-OS/AOS-CX indented configs.
    """
    header = re.compile(r"^\s*interface\s+(\S+)", re.IGNORECASE)
    cur_name = None
    cur: list[str] = []
    for line in content.splitlines():
        m = header.match(line)
        if m:
            if cur_name is not None:
                yield cur_name, "\n".join(cur)
            cur_name, cur = m.group(1), [line]
        elif cur_name is not None:
            if line.strip() and not line[0].isspace():
                yield cur_name, "\n".join(cur)
                cur_name, cur = None, []
            else:
                cur.append(line)
    if cur_name is not None:
        yield cur_name, "\n".join(cur)


def _canon(name: str) -> str:
    from apps.devices.topology import canonical_ifname
    return canonical_ifname(name or "").lower()


def extract_interface_block(content: str, interface_name: str) -> str:
    """Return the config stanza for ``interface_name`` (canonical-name aware)."""
    if not content:
        return ""
    want_raw = (interface_name or "").strip().lower()
    want_canon = _canon(interface_name)
    for name, block in _iter_interface_blocks(content):
        if name.lower() == want_raw or _canon(name) == want_canon:
            return block
    return ""


def _latest_config_content(device) -> str:
    from apps.configbackup.models import DeviceConfig
    cfg = DeviceConfig.objects.filter(device=device).order_by("-collected_at").first()
    return cfg.content if cfg and cfg.content else ""


def get_interface_config(device, interface_name: str) -> str:
    """Latest interface-specific config block for a device, or ''.

    Handles both CLI text (Cisco/Juniper/most vendors) and the AOS-CX REST API's
    JSON running-config (older backups stored as JSON), which would otherwise
    never yield an ``interface <name>`` stanza and always return ''. New AOS-CX
    backups are stored as CLI (see apps.devices.aos_cx_render), so they take the
    CLI path below."""
    from apps.devices.aos_cx_render import aos_cx_json_interface

    content = _latest_config_content(device)
    if not content:
        return ""
    if content.lstrip().startswith("{"):
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return aos_cx_json_interface(data, interface_name)
    return extract_interface_block(content, interface_name)


def _block_description(block: str) -> str:
    m = re.search(r"^\s*description\s+(.+)$", block, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


# ── checks ───────────────────────────────────────────────────────────────────
def run_check(check: dict, cfg: str) -> dict:
    """Evaluate one check against an interface's config block."""
    ctype = check.get("type")
    value = (check.get("value") or "")
    severity = check.get("severity", "warning")
    description = check.get("description") or value or ctype or "check"
    text = (cfg or "").lower()
    v = value.lower()

    if ctype == "config_contains":
        passed = v in text
    elif ctype == "config_not_contains":
        passed = v not in text
    elif ctype == "vlan_check":
        vlan_type = (check.get("vlan_type") or "access").lower()
        if vlan_type == "access":
            passed = "access" in text and "trunk" not in text
        elif vlan_type == "trunk":
            passed = "trunk" in text
        else:
            passed = vlan_type in text
    else:
        # Unknown check type — surface it but don't fail the interface.
        passed = True

    return {"type": ctype, "value": value, "description": description,
            "severity": severity, "passed": passed}


# ── trigger → matching interfaces ────────────────────────────────────────────
def _matched_interfaces(rule) -> list[tuple]:
    """Return ``(switch_device, local_interface, neighbor_label, match_label)``."""
    from apps.devices.models import Device, LLDPNeighbor

    trig = rule.trigger
    plat = (rule.platform or "").strip()
    out: list[tuple] = []

    if trig == "lldp_capability":
        from apps.devices.lldp import normalize_capabilities
        caps = set(normalize_capabilities([rule.trigger_value]))
        if not caps:
            return out
        # Compound matching: the neighbour must advertise the trigger capability,
        # AND all of `require`, and NONE of `exclude`. This disambiguates shared
        # capabilities — e.g. APs and switches both advertise "bridge", so an
        # uplink rule requires "router" (switches only) to skip AP ports.
        required = set(normalize_capabilities(rule.trigger_require_capabilities or []))
        excluded = set(normalize_capabilities(rule.trigger_exclude_capabilities or []))
        for nb in LLDPNeighbor.objects.select_related("seen_by"):
            # Normalise the stored capabilities too (defense-in-depth) so a record
            # collected before the normaliser learned a spelling (e.g. raw "wlan")
            # still matches the canonical token.
            nbcaps = set(normalize_capabilities(nb.capabilities or []))
            if not (caps & nbcaps):
                continue
            if required and not required.issubset(nbcaps):
                continue
            if excluded & nbcaps:
                continue
            label_caps = caps | required
            out.append((nb.seen_by, nb.local_interface,
                        nb.system_name or nb.chassis_id, ",".join(sorted(label_caps))))

    elif trig == "lldp_neighbor_platform":
        platforms = [p.strip() for p in rule.trigger_value.split(",") if p.strip()]
        ap_hostnames = set(Device.objects.filter(platform__in=platforms)
                           .values_list("hostname", flat=True))
        for nb in LLDPNeighbor.objects.select_related("seen_by", "matched_device"):
            md = nb.matched_device
            if (md and md.platform in platforms) or (nb.system_name and nb.system_name in ap_hostnames):
                out.append((nb.seen_by, nb.local_interface, nb.system_name,
                            md.platform if md else (platforms[0] if platforms else "")))

    elif trig == "lldp_neighbor_role":
        roles = [r.strip() for r in rule.trigger_value.split(",") if r.strip()]
        qs = LLDPNeighbor.objects.select_related("seen_by", "matched_device", "matched_device__role")
        for nb in qs:
            md = nb.matched_device
            if md and md.role and md.role.slug in roles:
                out.append((nb.seen_by, nb.local_interface, nb.system_name, md.role.slug))

    elif trig == "interface_name":
        # Match interfaces by NAME (not what's connected) — SVIs, LAGs,
        # port-channels, loopbacks, management, naming-convention uplinks, etc.
        try:
            rx = re.compile(rule.trigger_value, re.IGNORECASE)
        except re.error:
            logger.warning("interface-rule %s: bad regex %r", rule.name, rule.trigger_value)
            return out
        devs = Device.objects.filter(status="active")
        if plat:
            devs = devs.filter(platform=plat)
        for dev in devs:
            content = _latest_config_content(dev)
            if not content:
                continue
            for name, block in _iter_interface_blocks(content):
                if rx.search(name):
                    out.append((dev, name, name, "interface_name"))

    elif trig == "interface_description":
        try:
            rx = re.compile(rule.trigger_value)
        except re.error:
            logger.warning("interface-rule %s: bad regex %r", rule.name, rule.trigger_value)
            return out
        devs = Device.objects.all()
        if plat:
            devs = devs.filter(platform=plat)
        for dev in devs:
            content = _latest_config_content(dev)
            if not content:
                continue
            for name, block in _iter_interface_blocks(content):
                desc = _block_description(block)
                if desc and rx.search(desc):
                    out.append((dev, name, desc, "description"))

    elif trig == "manual":
        for token in rule.trigger_value.split(","):
            token = token.strip()
            if ":" not in token:
                continue
            host, ifname = token.split(":", 1)
            dev = Device.objects.filter(hostname=host.strip()).first()
            if dev:
                out.append((dev, ifname.strip(), "manual", "manual"))

    # The platform filter applies to the SWITCH (LLDP triggers; the
    # interface_name / interface_description branches already filtered above).
    if plat and trig not in ("interface_name", "interface_description"):
        out = [t for t in out if getattr(t[0], "platform", "") == plat]
    return out


# ── runner ───────────────────────────────────────────────────────────────────
def run_interface_compliance(rule, persist: bool = True) -> dict:
    """Run a rule across every matching interface; persist + return results."""
    from django.utils import timezone

    from .models import InterfaceComplianceResult

    now = timezone.now()
    if persist:
        InterfaceComplianceResult.objects.filter(rule=rule).delete()

    results = []
    for switch, ifname, neighbor_label, match_label in _matched_interfaces(rule):
        cfg = get_interface_config(switch, ifname)
        if not cfg:
            continue  # no config backup for this interface — can't assess
        checks = [run_check(c, cfg) for c in (rule.checks or [])]
        findings = [c for c in checks if not c["passed"]]
        passed = not findings
        results.append({
            "device_id": switch.id, "switch": switch.hostname, "interface": ifname,
            "neighbor": neighbor_label or "", "trigger_match": match_label or "",
            "checks": checks, "findings": findings, "passed": passed,
        })
        if persist:
            InterfaceComplianceResult.objects.create(
                rule=rule, device=switch, interface=ifname,
                neighbor=neighbor_label or "", trigger_match=match_label or "",
                passed=passed, findings=checks, checks_total=len(checks), checked_at=now)

    summary = {
        "matched": len(results),
        "passing": sum(1 for r in results if r["passed"]),
        "failing": sum(1 for r in results if not r["passed"]),
    }
    logger.info("interface-rule %s: %s", rule.name, summary)
    return {"rule_id": rule.id, "rule": rule.name, "summary": summary, "results": results}
