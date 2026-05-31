"""
Multi-vendor push-telemetry config generation.

Given a device, its collector IP, and its monitored interfaces, build the
platform-appropriate streaming-telemetry config: targeted device-level
subscriptions (CPU/memory/environment/BGP) plus one targeted subscription per
monitored interface (so we never subscribe to the whole interface tree).

Most platforms speak gNMI/MDT dial-out (Cisco IOS-XE/XR/NX-OS native; Juniper
JunOS analytics; Arista EOS via OpenConfig). Two don't and get their native
push config instead: Palo Alto PAN-OS → OTLP, Fortinet FortiOS → SNMP.

Intervals (recommendations):
  interfaces : 30s    CPU/memory : 30s    environment : 60s    BGP : 100s

The IETF subscription model (IOS-XE/XR/NX-OS) takes the period in centiseconds
(periodic 3000 == 30s); the OpenConfig/JunOS/EOS variants use seconds.
"""
from __future__ import annotations

# Recommended periods, in seconds. Converted per-platform as needed.
INTERVAL_INTERFACE_S = 30
INTERVAL_CPU_S = 30
INTERVAL_MEM_S = 30
INTERVAL_ENV_S = 60
INTERVAL_BGP_S = 100

_RECEIVER_PORT = 57400


def _xe_sub(sub_id: int, xpath: str, period_s: int, collector_ip: str) -> str:
    """One IOS-XE/NX-OS `telemetry ietf subscription` block (centisecond period)."""
    return (
        f"telemetry ietf subscription {sub_id}\n"
        f" encoding encode-kvgpb\n"
        f" filter xpath {xpath}\n"
        f" stream yang-push\n"
        f" update-policy periodic {period_s * 100}\n"
        f" receiver ip address {collector_ip} {_RECEIVER_PORT} protocol grpc-tcp"
    )


def _header(device, collector_ip: str, interfaces: list) -> str:
    n = len(interfaces)
    return (
        f"! NetPulse gNMI Telemetry Configuration\n"
        f"! Generated for {device.hostname or device.ip_address} "
        f"({device.platform or 'unknown'}) - {n} monitored interface"
        f"{'' if n == 1 else 's'}\n"
        f"! Collector: {collector_ip}:{_RECEIVER_PORT}"
    )


# Per-platform device-level subscription tables: (xpath, period_seconds, label).
# Interface xpath uses {if} as the interface-name placeholder.
_XE_DEVICE = [
    ("/process-cpu-ios-xe-oper:cpu-usage/cpu-utilization", INTERVAL_CPU_S, "CPU"),
    ("/memory-ios-xe-oper:memory-statistics/memory-statistic", INTERVAL_MEM_S, "Memory"),
    ("/environment-ios-xe-oper:environment-sensors/environment-sensor", INTERVAL_ENV_S, "Environment"),
    ("/bgp-ios-xe-oper:bgp-state-data/neighbors/neighbor", INTERVAL_BGP_S, "BGP"),
]
_XE_IFACE = "/interfaces-ios-xe-oper:interfaces/interface[name='{ifname}']"

_XR_DEVICE = [
    ("/Cisco-IOS-XR-wdsysmon-fd-oper:system-monitoring/cpu-utilization", INTERVAL_CPU_S, "CPU"),
    ("/Cisco-IOS-XR-nto-misc-oper:memory-summary/nodes/node/summary", INTERVAL_MEM_S, "Memory"),
    ("/Cisco-IOS-XR-sysadmin-envmon-ui:environment/oper", INTERVAL_ENV_S, "Environment"),
    ("/Cisco-IOS-XR-ipv4-bgp-oper:bgp/instances/instance/instance-active/default-vrf/neighbors/neighbor",
     INTERVAL_BGP_S, "BGP"),
]
_XR_IFACE = "/Cisco-IOS-XR-infra-statsd-oper:infra-statistics/interfaces/interface[interface-name='{ifname}']"

# NX-OS uses the IETF subscription model with OpenConfig device paths.
_NXOS_DEVICE = [
    ("/System/procsys/sysload", INTERVAL_CPU_S, "CPU/Load"),
    ("/System/procsys", INTERVAL_MEM_S, "Memory"),
    ("/System/ch", INTERVAL_ENV_S, "Environment"),
    ("/System/bgp-items", INTERVAL_BGP_S, "BGP"),
]
_NXOS_IFACE = "/System/intf-items/phys-items/PhysIf-list[id='{ifname}']"


def _ietf_generator(device_table, iface_xpath):
    """Build a generator for IETF-subscription platforms (IOS-XE/XR, NX-OS)."""

    def _gen(device, collector_ip, interfaces, cfg=None):
        blocks = [_header(device, collector_ip, interfaces), "! --- Device Health ---"]
        sub = 100
        for xpath, period, _label in device_table:
            blocks.append(_xe_sub(sub, xpath, period, collector_ip))
            sub += 1
        blocks.append("! --- Monitored Interfaces ---")
        if interfaces:
            sub = 200
            for iface in interfaces:
                blocks.append(
                    _xe_sub(sub, iface_xpath.format(ifname=iface.if_name), INTERVAL_INTERFACE_S, collector_ip)
                )
                sub += 1
        else:
            blocks.append(
                "! No monitored interfaces - discover interfaces first to generate\n"
                "! targeted subscriptions. Falling back to ALL interfaces:"
            )
            # Whole-tree fallback: strip the per-interface predicate entirely.
            all_xpath = iface_xpath.split("[", 1)[0]
            blocks.append(_xe_sub(200, all_xpath, INTERVAL_INTERFACE_S, collector_ip))
        return "\n!\n".join(blocks)

    return _gen


generate_iosxe_gnmi = _ietf_generator(_XE_DEVICE, _XE_IFACE)
generate_iosxr_gnmi = _ietf_generator(_XR_DEVICE, _XR_IFACE)
generate_nxos_gnmi = _ietf_generator(_NXOS_DEVICE, _NXOS_IFACE)


def generate_junos_gnmi(device, collector_ip, interfaces, cfg=None):
    """Juniper JunOS analytics (gRPC dial-out) sensor config (OpenConfig paths)."""
    mgmt = str(device.management_ip or device.ip_address or "<MGMT_IP>")
    lines = [
        _header(device, collector_ip, interfaces),
        "set services analytics streaming-server NetPulse "
        f"remote-address {collector_ip}",
        f"set services analytics streaming-server NetPulse remote-port {_RECEIVER_PORT}",
        "set services analytics export-profile NetPulse-Profile "
        f"local-address {mgmt}",
        f"set services analytics export-profile NetPulse-Profile reporting-rate {INTERVAL_INTERFACE_S}",
        "set services analytics export-profile NetPulse-Profile format gpb",
        "set services analytics export-profile NetPulse-Profile transport grpc",
    ]

    def sensor(name, resource):
        return (
            f"set services analytics sensor {name} server-name NetPulse\n"
            f"set services analytics sensor {name} export-name NetPulse-Profile\n"
            f"set services analytics sensor {name} resource {resource}"
        )

    lines.append(sensor("CPU-Memory", "/components/component[name='Routing Engine']/state"))
    lines.append(sensor("Environment", "/components/component/state/temperature"))
    lines.append(sensor(
        "BGP",
        "/network-instances/network-instance/protocols/protocol/bgp/neighbors/",
    ))
    if interfaces:
        for iface in interfaces:
            safe = iface.if_name.replace("/", "_").replace(".", "_")
            lines.append(sensor(f"Interface-{safe}", f"/interfaces/interface[name='{iface.if_name}']/"))
    else:
        lines.append("# No monitored interfaces - falling back to all interfaces")
        lines.append(sensor("Interfaces-All", "/interfaces/"))
    return "\n".join(lines)


def generate_eos_gnmi(device, collector_ip, interfaces, cfg=None):
    """Arista EOS OpenConfig gNMI dial-out via the TerminAttr agent."""
    lines = [
        _header(device, collector_ip, interfaces),
        "! Enable the TerminAttr agent to stream OpenConfig state to NetPulse.",
        "daemon TerminAttr",
        "   exec /usr/bin/TerminAttr"
        f" -grpcaddr MGMT/0.0.0.0:6030 -gnmisub=oneof,{collector_ip}:{_RECEIVER_PORT} -taillogs",
        "   no shutdown",
        "!",
        "! Subscribed OpenConfig paths:",
        "!   CPU/Memory : /components/component/state",
        "!   Environment: /components/component/state/temperature",
        "!                /components/component/fans/fan/state",
        "!   BGP        : /network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state",
    ]
    if interfaces:
        lines.append("!   Interfaces :")
        for iface in interfaces:
            lines.append(f"!     /interfaces/interface[name='{iface.if_name}']/state/counters")
    else:
        lines.append("! No monitored interfaces - falling back to /interfaces/interface/state/counters")
    return "\n".join(lines)


def generate_panos_otlp(device, collector_ip, interfaces, cfg=None):
    """Palo Alto PAN-OS streams via OpenTelemetry (OTLP), not gNMI."""
    return "\n".join([
        _header(device, collector_ip, interfaces),
        "! PAN-OS streams telemetry via OpenTelemetry (OTLP) - not gNMI.",
        "! Point it at the NetPulse OTLP receiver (ingest-otlp, port 4317).",
        "set deviceconfig system telemetry application performance",
        "set deviceconfig system telemetry application enable yes",
        f"set deviceconfig system telemetry endpoint address {collector_ip}",
        "set deviceconfig system telemetry endpoint port 4317",
        "set deviceconfig system telemetry endpoint protocol grpc",
    ])


def generate_fortios_snmp(device, collector_ip, interfaces, cfg=None):
    """Fortinet FortiOS has no gNMI - fall back to SNMP polling config."""
    return "\n".join([
        _header(device, collector_ip, interfaces),
        "! FortiOS does not support gNMI. Use SNMP polling and Syslog for telemetry.",
        "config system snmp community",
        "    edit 1",
        '        set name "netpulse"',
        "        set status enable",
        "        config hosts",
        "            edit 1",
        f"                set ip {collector_ip}/32",
        "            next",
        "        end",
        "    next",
        "end",
    ])


def generate_generic_gnmi(device, collector_ip, interfaces, cfg=None):
    """Best-effort OpenConfig paths for unknown platforms."""
    lines = [
        _header(device, collector_ip, interfaces),
        "! Generic OpenConfig paths (best-effort - may not work on all platforms).",
        "!   /system/state",
        "!   /components/component/state",
    ]
    if interfaces:
        for iface in interfaces:
            lines.append(f"!   /interfaces/interface[name='{iface.if_name}']/state/counters")
    else:
        lines.append("!   /interfaces/interface/state/counters")
    return "\n".join(lines)


# device.platform → generator. Cisco IOS shares the IOS-XE generator.
SNIPPET_GENERATORS = {
    "ios": generate_iosxe_gnmi,
    "ios_xe": generate_iosxe_gnmi,
    "ios_xr": generate_iosxr_gnmi,
    "nxos": generate_nxos_gnmi,
    "junos": generate_junos_gnmi,
    "eos": generate_eos_gnmi,
    "panos": generate_panos_otlp,
    "fortios": generate_fortios_snmp,
}


def generate_push_config(device, collector_ip, interfaces, cfg=None) -> str:
    """Dispatch to the platform's push-telemetry generator (generic fallback)."""
    gen = SNIPPET_GENERATORS.get((device.platform or "").lower(), generate_generic_gnmi)
    return gen(device, collector_ip, interfaces, cfg)
