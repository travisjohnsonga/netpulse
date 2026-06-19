"""Render AOS-CX REST running-config JSON as human-readable CLI text.

AOS-CX config backups come from the REST API as a structured JSON document
(top-level ``System`` / ``VLAN`` / ``Interface`` / ``Port`` / … sections), not
CLI text. Raw JSON is hard to read and produces noisy, key-ordering diffs. This
module converts that JSON to AOS-CX-style CLI text, used in three places:

  1. collector — new backups are stored as CLI (``_fetch_aos_cx_via_rest``),
  2. display/diff — existing JSON backups are rendered on the fly
     (``render_config_content`` → DeviceConfig ``rendered_content``),
  3. compliance — a single interface block is extracted for the interface
     engine (``aos_cx_json_interface``, used by interface_compliance).

Field names are the REAL running-config keys (verified live): description lives
in ``Interface[<urlenc>]``; vlan_mode/vlan_tag/vlan_trunks/stp_config/
loop_protect_enable/admin in ``Port[<urlenc>]``; booleans arrive as the strings
"true"/"false"; interface keys are URL-encoded ("1%2F1%2F14").
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote


def _truthy(v) -> bool:
    """AOS-CX REST encodes booleans as the strings 'true'/'false' (and sometimes
    real bools); treat both forms uniformly."""
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def render_interface(name: str, data: dict) -> str:
    """Render one AOS-CX interface (merged Interface+Port dict) as AOS-CX-style
    pseudo-CLI, so the substring ``config_contains`` / ``vlan_check`` checks
    evaluate identically to a CLI config (access → "vlan access N"; trunk /
    native-* → "vlan trunk …"; stp_config → "spanning-tree …")."""
    if not isinstance(data, dict):
        return ""
    lines = [f"interface {name}"]

    desc = data.get("description")
    if desc:
        lines.append(f"    description {desc}")

    if str(data.get("admin", "")).strip().lower() == "down":
        lines.append("    shutdown")
    else:
        lines.append("    no shutdown")

    vlan_mode = (data.get("vlan_mode") or "").lower()
    vlan_tag = data.get("vlan_tag")
    trunks = data.get("vlan_trunks") or []
    if vlan_mode == "access":
        if vlan_tag:
            lines.append(f"    vlan access {vlan_tag}")
    elif vlan_mode in ("trunk", "native-tagged", "native-untagged"):
        if vlan_tag:
            lines.append(f"    vlan trunk native {vlan_tag}")
        if trunks:
            lines.append("    vlan trunk allowed " + ",".join(str(v) for v in trunks))
        lines.append("    vlan trunk mode")

    stp = data.get("stp_config")
    if isinstance(stp, dict):
        if _truthy(stp.get("bpdu_guard_enable")):
            lines.append("    spanning-tree bpdu-guard")
        if _truthy(stp.get("admin_edge_port_enable")):
            lines.append("    spanning-tree port-type admin-edge")
    if _truthy(data.get("loop_protect_enable")):
        lines.append("    loop-protect")

    return "\n".join(lines)


def _find_aos_entry(section, interface_name: str) -> dict:
    """Look up one interface in an AOS-CX config section (URL-encoded keys, e.g.
    ``1%2F1%2F14``): encoded key, plain name, a match on the entry ``name``
    field, then an unquote of each key."""
    if not isinstance(section, dict):
        return {}
    enc = quote(interface_name, safe="")
    if isinstance(section.get(enc), dict):
        return section[enc]
    if isinstance(section.get(interface_name), dict):
        return section[interface_name]
    for v in section.values():
        if isinstance(v, dict) and v.get("name") == interface_name:
            return v
    for k, v in section.items():
        if isinstance(v, dict) and unquote(k) == interface_name:
            return v
    return {}


def _interface_sections(data: dict) -> tuple[dict, dict]:
    """Return (ports, interfaces) sections, tolerating a flatter top-level layout
    where the whole object is the interface map (no Port/Interface split)."""
    ports = data.get("Port") if isinstance(data.get("Port"), dict) else {}
    ifaces = data.get("Interface") if isinstance(data.get("Interface"), dict) else {}
    if not ports and not ifaces:
        # Flat layout: top-level keyed by interface. Only entries that look like
        # an interface (have interface-ish fields) are treated as such.
        flat = {k: v for k, v in data.items()
                if isinstance(v, dict)
                and any(f in v for f in ("vlan_mode", "vlan_tag", "stp_config", "admin"))}
        return flat, {}
    return ports, ifaces


def aos_cx_json_interface(data: dict, interface_name: str) -> str:
    """Extract + render one interface from an AOS-CX REST running-config JSON,
    merging the Interface (description) and Port (vlan/stp/admin) sections."""
    ports, ifaces = _interface_sections(data)
    port = _find_aos_entry(ports, interface_name)
    iface = _find_aos_entry(ifaces, interface_name)
    if not port and not iface:
        return ""
    merged = {**iface, **port}  # Port (vlan/stp) wins; description from Interface
    return render_interface(interface_name, merged)


def _vlan_sort_key(item):
    try:
        return (0, int(item[0]))
    except (TypeError, ValueError):
        return (1, str(item[0]))


def _ifkey_sort(key: str):
    name = unquote(key)
    nums = tuple(int(p) for p in re.split(r"[/.:]", name) if p.isdigit())
    return (nums or (0,), name)


def aos_cx_json_to_cli(data: dict) -> str:
    """Convert a full AOS-CX REST running-config JSON document to CLI-like text:
    hostname, VLANs (id + name), then every interface (merged Interface+Port),
    each block terminated by ``!``. Returns "" for non-dict input."""
    if not isinstance(data, dict):
        return ""
    lines: list[str] = []

    hostname = (data.get("System") or {}).get("hostname") or data.get("hostname")
    if hostname:
        lines += [f"hostname {hostname}", "!"]

    vlans = data.get("VLAN") if isinstance(data.get("VLAN"), dict) else (
        data.get("vlans") if isinstance(data.get("vlans"), dict) else {})
    if vlans:
        for vid, vd in sorted(vlans.items(), key=_vlan_sort_key):
            lines.append(f"vlan {vid}")
            name = (vd or {}).get("name")
            if name and str(name) != str(vid):
                lines.append(f"    name {name}")
        lines.append("!")

    ports, ifaces = _interface_sections(data)
    keys = list(dict.fromkeys(list(ports.keys()) + list(ifaces.keys())))
    for k in sorted(keys, key=_ifkey_sort):
        port = ports.get(k) if isinstance(ports.get(k), dict) else {}
        iface = ifaces.get(k) if isinstance(ifaces.get(k), dict) else {}
        merged = {**iface, **port}
        name = merged.get("name") or unquote(k)
        block = render_interface(name, merged)
        if block:
            lines += [block, "!"]

    return "\n".join(lines)


def render_config_content(content: str, platform: str) -> str:
    """Human-readable CLI for display/diff. AOS-CX configs stored as JSON are
    converted; everything else (already CLI) is returned unchanged."""
    if not content:
        return content
    if (platform or "").lower() == "aos_cx" and content.lstrip().startswith("{"):
        try:
            rendered = aos_cx_json_to_cli(json.loads(content))
            if rendered:
                return rendered
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return content
