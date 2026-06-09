"""
Ubiquiti UniFi syslog normalization (CEF format).

UniFi controllers emit syslog in ArcSight CEF (Common Event Format)::

    CEF:0|Ubiquiti|UniFi Network|{ver}|{event_id}|{event_name}|{sev}|{k=v pairs}

e.g. WiFi Client Connected (400), Disconnected (401), Config Modified (546).
The extension is space-separated ``key=value`` pairs whose values may contain
spaces (delimited by the next ``key=``). These functions detect that shape,
parse the header + extensions, synthesise a clean human-readable message +
syslog severity + program label, and surface per-event detail (client, AP,
RSSI/signal quality, SSID, config change, device) into ``extras`` for search,
filtering and alerting. All functions are pure; the parser applies them after
RFC 3164/5424 parsing (mirrors ingest/fortios.py and ingest/aos_cx.py).

Detection/parsing runs against the *raw* line, not the cleaned message: the
RFC 3164 parser splits the leading ``CEF:`` token into ``app_name`` (it looks
like a ``TAG:``), so the cleaned message loses the ``CEF:`` prefix.
"""
from __future__ import annotations

import re
from typing import Any

# CEF header: CEF:Version|Vendor|Product|DevVersion|EventID|Name|Severity|Extension
_CEF_RE = re.compile(
    r"CEF:(?P<cef_version>\d+)\|(?P<vendor>[^|]*)\|(?P<product>[^|]*)\|"
    r"(?P<dev_version>[^|]*)\|(?P<event_id>[^|]*)\|(?P<name>[^|]*)\|"
    r"(?P<severity>[^|]*)\|(?P<extensions>.*)$",
    re.DOTALL,
)

# CEF extension key boundary: a key= at the start or after whitespace. Values
# (which may contain spaces) run up to the next such boundary. Requiring the key
# to follow whitespace avoids matching '=' embedded inside a value.
_EXT_KEY_RE = re.compile(r"(?:^|\s)([A-Za-z][\w.]*)=")

# UniFi CEF event id → stable event_type slug.
UNIFI_EVENT_TYPES: dict[str, str] = {
    "400": "wifi_client_connected",
    "401": "wifi_client_disconnected",
    "402": "wifi_client_roamed",
    "403": "wifi_client_blocked",
    "404": "wifi_client_unblocked",
    "500": "device_connected",
    "501": "device_disconnected",
    "502": "device_adopted",
    "503": "device_lost",
    "546": "config_modified",
    "547": "admin_login",
    "548": "admin_logout",
}

# CEF severity (UniFi uses 1–7) → numeric syslog severity (RFC 5424 scale).
_CEF_SEVERITY_TO_SYSLOG: dict[str, int] = {
    "0": 6, "1": 6, "2": 6, "3": 5, "4": 4,
    "5": 4, "6": 3, "7": 2, "8": 2, "9": 1, "10": 0,
}

# UNIFIwifiBand value → human label.
_BAND = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz"}

# Event types that carry WiFi-client detail.
_WIFI_EVENTS = {
    "wifi_client_connected", "wifi_client_disconnected",
    "wifi_client_roamed", "wifi_client_blocked", "wifi_client_unblocked",
}
# Event types that carry adopted-device detail.
_DEVICE_EVENTS = {
    "device_connected", "device_disconnected",
    "device_adopted", "device_lost",
}


def is_unifi_log(text: str) -> bool:
    """True if the text looks like a UniFi (Ubiquiti) CEF record."""
    if not text:
        return False
    return "CEF:" in text and "Ubiquiti" in text


def parse_cef_extensions(ext: str) -> dict[str, str]:
    """
    Parse CEF extension ``key=value`` pairs into a flat dict. Values may contain
    spaces; each value runs from after its ``key=`` to the next ``key=`` boundary.
    """
    out: dict[str, str] = {}
    matches = list(_EXT_KEY_RE.finditer(ext or ""))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(ext)
        out[m.group(1)] = ext[start:end].strip()
    return out


def parse_cef(text: str) -> dict[str, Any] | None:
    """Parse a CEF line into ``{header fields…, "ext": {extensions}}`` or None."""
    m = _CEF_RE.search(text or "")
    if not m:
        return None
    d = m.groupdict()
    d["ext"] = parse_cef_extensions(d.pop("extensions") or "")
    return d


def map_unifi_severity(cef_severity: str) -> int | None:
    """CEF severity → numeric syslog severity, or None if unrecognised."""
    return _CEF_SEVERITY_TO_SYSLOG.get((cef_severity or "").strip())


def classify_signal(rssi: int | None) -> str:
    """Classify an RSSI value (dBm) into a signal-quality bucket."""
    if rssi is None:
        return ""
    if rssi >= -60:
        return "excellent"
    if rssi >= -70:
        return "good"
    if rssi >= -80:
        return "fair"
    if rssi >= -85:
        return "poor"
    return "very_poor"


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_wifi_client_event(ext: dict[str, str]) -> dict[str, Any]:
    """Extract client / AP / radio detail from a WiFi-client CEF event."""
    # AP info: "connected" keys on connect, "lastConnected" on disconnect.
    ap_name = ext.get("UNIFIconnectedToDeviceName") or ext.get("UNIFIlastConnectedToDeviceName", "")
    ap_ip = ext.get("UNIFIconnectedToDeviceIp") or ext.get("UNIFIlastConnectedToDeviceIp", "")
    ap_mac = ext.get("UNIFIconnectedToDeviceMac") or ext.get("UNIFIlastConnectedToDeviceMac", "")
    ap_model = ext.get("UNIFIconnectedToDeviceModel") or ext.get("UNIFIlastConnectedToDeviceModel", "")
    rssi = ext.get("UNIFIWiFiRssi") or ext.get("UNIFIlastConnectedToWiFiRssi", "")
    rssi_int = _int_or_none(rssi)

    return {
        # Client
        "client_hostname": ext.get("UNIFIclientHostname", ""),
        "client_alias": ext.get("UNIFIclientAlias", ""),
        "client_ip": ext.get("UNIFIclientIp", ""),
        "client_mac": ext.get("UNIFIclientMac", ""),
        # AP
        "ap_name": ap_name,
        "ap_ip": ap_ip,
        "ap_mac": ap_mac,
        "ap_model": ap_model,
        # WiFi / radio
        "ssid": ext.get("UNIFIwifiName", ""),
        "channel": ext.get("UNIFIwifiChannel", ""),
        "channel_width": ext.get("UNIFIwifiChannelWidth", ""),
        "band": _BAND.get(ext.get("UNIFIwifiBand", ""), ext.get("UNIFIwifiBand", "")),
        "rssi_dbm": rssi_int,
        "signal_quality": classify_signal(rssi_int),
        "auth_method": ext.get("UNIFIauthMethod", ""),
        # Network
        "network_name": ext.get("UNIFInetworkName", ""),
        "vlan": ext.get("UNIFInetworkVlan", ""),
        "subnet": ext.get("UNIFInetworkSubnet", ""),
        # Session (disconnect)
        "duration": ext.get("UNIFIduration", ""),
        "usage_down": ext.get("UNIFIusageDown", ""),
        "usage_up": ext.get("UNIFIusageUp", ""),
    }


def _parse_config_event(ext: dict[str, str]) -> dict[str, Any]:
    """Extract config-change detail from a Config Modified CEF event."""
    return {
        "admin": ext.get("UNIFIadmin", ""),
        "access_method": ext.get("UNIFIaccessMethod", ""),
        "settings_section": ext.get("UNIFIsettingsSection", ""),
        "settings_entry": ext.get("UNIFIsettingsEntry", ""),
        "changes": ext.get("UNIFIsettingsChanges", ""),
        "src_ip": ext.get("src", ""),
    }


def _parse_device_event(ext: dict[str, str]) -> dict[str, Any]:
    """Extract adopted-device detail from a device CEF event."""
    return {
        "device_name": ext.get("UNIFIdeviceName", ""),
        "device_ip": ext.get("UNIFIdeviceIp", ""),
        "device_mac": ext.get("UNIFIdeviceMac", ""),
        "device_model": ext.get("UNIFIdeviceModel", ""),
        "device_version": ext.get("UNIFIdeviceVersion", ""),
    }


def _client_label(f: dict[str, Any]) -> str:
    return f.get("client_alias") or f.get("client_hostname") or f.get("client_mac") or "client"


def _radio_detail(f: dict[str, Any]) -> str:
    """Build "(Ch.104 5 GHz 20MHz, RSSI -76 dBm fair)" — skipping empties."""
    radio_bits = []
    if f.get("channel"):
        radio_bits.append(f"Ch.{f['channel']}")
    if f.get("band"):
        radio_bits.append(f["band"])
    if f.get("channel_width"):
        radio_bits.append(f"{f['channel_width']}MHz")
    radio = " ".join(radio_bits)
    if f.get("rssi_dbm") is not None:
        sig = f"RSSI {f['rssi_dbm']} dBm"
        if f.get("signal_quality"):
            sig += f" {f['signal_quality']}"
        radio = f"{radio}, {sig}" if radio else sig
    return f"({radio})" if radio else ""


def format_unifi_message(event_type: str, header: dict[str, Any], f: dict[str, Any]) -> str:
    """Build a concise human-readable message for a normalized UniFi event."""
    if event_type in _WIFI_EVENTS:
        client = _client_label(f)
        ap = f.get("ap_name") or f.get("ap_mac") or "AP"
        verb = {
            "wifi_client_connected": f"connected to {ap}",
            "wifi_client_disconnected": f"disconnected from {ap}",
            "wifi_client_roamed": f"roamed to {ap}",
            "wifi_client_blocked": f"blocked on {ap}",
            "wifi_client_unblocked": f"unblocked on {ap}",
        }.get(event_type, f"on {ap}")
        parts = [f"{client} {verb}"]
        detail = _radio_detail(f)
        if detail:
            parts.append(detail)
        if f.get("ssid"):
            parts.append(f"SSID {f['ssid']}")
        if event_type == "wifi_client_disconnected" and f.get("duration"):
            parts.append(f"duration {f['duration']}s")
        return " ".join(parts)

    if event_type == "config_modified":
        base = f"Config changed by {f.get('admin') or 'admin'}"
        if f.get("access_method"):
            base += f" via {f['access_method']}"
        loc = " → ".join(p for p in (f.get("settings_section"), f.get("settings_entry")) if p)
        if loc:
            base += f": {loc}"
        if f.get("changes"):
            base += f" ({f['changes']})"
        return base

    if event_type in _DEVICE_EVENTS:
        name = f.get("device_name") or f.get("device_mac") or "device"
        verb = {
            "device_connected": "connected",
            "device_disconnected": "disconnected",
            "device_adopted": "adopted",
            "device_lost": "lost",
        }.get(event_type, event_type)
        return f"{name} {verb}"

    # admin_login/logout and any other event: fall back to the CEF event name.
    return header.get("name") or event_type


def map_unifi_program(event_type: str) -> str:
    """Event type → uppercase program label."""
    if event_type in _WIFI_EVENTS:
        return "WIRELESS"
    if event_type in _DEVICE_EVENTS:
        return "DEVICE"
    if event_type == "config_modified":
        return "CONFIG"
    if event_type in ("admin_login", "admin_logout"):
        return "AUTH"
    return "UNIFI"


def unifi_extras(event_type: str, f: dict[str, Any]) -> dict[str, Any]:
    """Selected per-event fields surfaced into extras for search/filtering."""
    out: dict[str, Any] = {}
    for key, value in f.items():
        if value is None or value == "":
            continue
        out[f"unifi_{key}"] = value
    return out


def normalize(result: dict[str, Any], severities: dict[int, str]) -> None:
    """
    Mutate a parsed syslog `result` in place to normalise a UniFi CEF record.
    Parses the original `result["raw"]` (the RFC 3164 parser strips the leading
    ``CEF:`` token off `message`); `raw` is preserved. `severities` is the
    parser's numeric→name severity table.
    """
    parsed = parse_cef(result.get("raw") or result.get("message") or "")
    if not parsed or parsed.get("vendor") != "Ubiquiti":
        return

    event_id = (parsed.get("event_id") or "").strip()
    event_type = UNIFI_EVENT_TYPES.get(event_id, "unifi_event")
    ext = parsed["ext"]

    sev = map_unifi_severity(parsed.get("severity", ""))
    if sev is not None:
        result["severity"] = sev
        result["severity_name"] = severities.get(sev, str(sev))

    if event_type in _WIFI_EVENTS:
        fields = _parse_wifi_client_event(ext)
    elif event_type == "config_modified":
        fields = _parse_config_event(ext)
    elif event_type in _DEVICE_EVENTS:
        fields = _parse_device_event(ext)
    else:
        fields = {}

    result["message"] = format_unifi_message(event_type, parsed, fields)
    program = map_unifi_program(event_type)
    result["program"] = program
    result["app_name"] = "UniFi"
    result["vendor"] = "ubiquiti"

    extras = result.get("extras") or {}
    extras["unifi_event_type"] = event_type
    if event_id:
        extras["unifi_event_id"] = event_id
    if parsed.get("name"):
        extras["unifi_event_name"] = parsed["name"]
    if ext.get("UNIFIhost"):
        extras["unifi_controller_host"] = ext["UNIFIhost"]
    extras.update(unifi_extras(event_type, fields))
    result["extras"] = extras
