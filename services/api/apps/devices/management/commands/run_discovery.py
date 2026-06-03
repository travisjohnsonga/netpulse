"""
Device discovery management command.

Usage:
  python manage.py run_discovery --job <job_id>
  python manage.py run_discovery --scan 10.0.0.0/24 --name "Office scan"
  python manage.py run_discovery --topology --seed 10.0.0.1 --name "Core walk"

Discovery tiers implemented:
  Tier 2 — Topology Walk: route-table next-hop recursion + CDP/LLDP
  Tier 3 — Active Scan: SNMP sysDescr → SSH banner → HTTP probes

All discovered devices land in PENDING state — never auto-activate.

OT/ICS WARNING: Always exclude OT subnets. Probing industrial controllers
can cause physical damage. The --excluded-subnets flag is critical.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone as dj_tz

from apps.devices.models import DiscoveredDevice, DiscoveryJob

logger = logging.getLogger(__name__)

# ── SNMP OIDs for fingerprinting ──────────────────────────────────────────────
_OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
_OID_SYS_NAME  = "1.3.6.1.2.1.1.5.0"
_OID_SYS_OBJID = "1.3.6.1.2.1.1.2.0"         # sysObjectID (enterprise OID)
_OID_LLDP_REM  = "1.0.8802.1.1.2.1.4.1.1"   # lldpRemTable
_OID_CDP_CACHE = "1.3.6.1.4.1.9.9.23.1.2.1" # cdpCacheTable
_OID_ROUTE_TBL = "1.3.6.1.2.1.4.24.4"        # ipCidrRouteTable (RFC 2096)

# sysObjectID enterprise prefix (1.3.6.1.4.1.<N>) → vendor.
_ENTERPRISE_VENDORS = {
    "9":     "cisco",
    "2636":  "juniper",
    "12356": "fortinet",
    "30065": "arista",
    "25461": "paloalto",
    "14988": "mikrotik",
    "2011":  "huawei",
    "14823": "aruba",
    "47196": "aruba",   # HPE Networking (Aruba AOS-CX, e.g. 6100/6300)
}


def _vendor_from_sysobjid(oid: str) -> str:
    """Map a sysObjectID (1.3.6.1.4.1.<enterprise>.…) to a vendor."""
    prefix = "1.3.6.1.4.1."
    if not oid.startswith(prefix):
        return ""
    enterprise = oid[len(prefix):].split(".", 1)[0]
    return _ENTERPRISE_VENDORS.get(enterprise, "")


# sysObjectID enterprise prefix → NetPulse platform, when the enterprise OID
# pins the platform unambiguously (used as a fallback when sysDescr can't).
_ENTERPRISE_PLATFORMS = {
    "47196": "aos_cx",   # HPE Networking — Aruba AOS-CX (6100/6300/…)
}


def _platform_from_sysobjid(oid: str) -> str:
    """Map a sysObjectID enterprise prefix to a NetPulse platform ('' if none)."""
    prefix = "1.3.6.1.4.1."
    if not oid.startswith(prefix):
        return ""
    enterprise = oid[len(prefix):].split(".", 1)[0]
    return _ENTERPRISE_PLATFORMS.get(enterprise, "")

# Vendor → fingerprint substrings in sysDescr (matched case-insensitively).
# FortiGate sysDescr reads "FortiGate-…"/"FortiOS …" and rarely the literal
# "fortinet", so all three spellings map to fortinet.
_VENDOR_PATTERNS = [
    ("cisco",     ("cisco",)),
    ("juniper",   ("juniper", "junos")),
    ("arista",    ("arista",)),
    ("sonicwall", ("sonicwall", "sonicos")),
    # AOS-CX switches brand either as "Aruba…" or "HPE ANW/HPE Aruba …".
    ("aruba",     ("aruba", "arubaos", "hpe anw", "hpe aruba")),
    ("fortinet",  ("fortios", "fortigate", "fortinet")),
    ("paloalto",  ("palo alto", "pan-os")),
    ("mikrotik",  ("mikrotik", "routeros")),
    ("huawei",    ("huawei",)),
]


def _vendor_from_descr(descr: str) -> str:
    low = descr.lower()
    for vendor, patterns in _VENDOR_PATTERNS:
        if any(p in low for p in patterns):
            return vendor
    return ""


# SSH identification banner → vendor (best-effort hint; full platform ID happens
# at device-add time via Netmiko SSHDetect / show version).
_BANNER_VENDORS = [
    ("cisco", "cisco"), ("arista", "arista"), ("juniper", "juniper"),
    ("fortinet", "fortinet"), ("forti", "fortinet"), ("paloalto", "paloalto"),
    ("sonicwall", "sonicwall"), ("aruba", "aruba"),
    ("mikrotik", "mikrotik"), ("huawei", "huawei"), ("vyos", "vyos"),
]


def _vendor_from_banner(banner: str) -> str:
    low = banner.lower()
    for needle, vendor in _BANNER_VENDORS:
        if needle in low:
            return vendor
    return ""


def _platform_from_descr(descr: str) -> str:
    """
    Best-effort NetPulse platform string from an SNMP sysDescr. IOS-XE / IOS-XR
    must be matched before plain IOS (their sysDescr also contains "IOS"), and
    we accept the hyphen, space and no-separator spellings ("IOS-XE", "IOS XE",
    "IOSXE").
    """
    low = descr.lower()
    if "nx-os" in low or "nexus" in low:
        return "nxos"
    if "ios xr" in low or "ios-xr" in low or "iosxr" in low:
        return "ios_xr"
    if "ios xe" in low or "ios-xe" in low or "iosxe" in low:
        return "ios_xe"
    if "cisco ios" in low or "ios software" in low or "ios (tm)" in low:
        return "ios"
    if "fortios" in low or "fortigate" in low or "fortinet" in low:
        return "fortios"
    if "pan-os" in low or "palo alto" in low:
        return "panos"
    if "sonicos" in low or "sonicwall" in low:
        return "sonicwall"
    # AOS-CX must be matched before generic ArubaOS (its sysDescr contains both).
    # Some AOS-CX switches brand as "HPE ANW …"/"HPE Aruba …" with no AOS-CX
    # token (e.g. "HPE ANW R9Y04A 6100 … PL.10.16.1030").
    if ("aos-cx" in low or "arubaos-cx" in low
            or "hpe anw" in low or "hpe aruba" in low):
        return "aos_cx"
    if "arubaos" in low or "aruba networks" in low or "aruba" in low:
        return "aruba"
    if "junos" in low or "juniper" in low:
        return "junos"
    if "arista" in low or " eos" in low:
        return "eos"
    return ""


# Vendor → default NetPulse platform, used when the vendor is known but the
# platform couldn't be parsed from sysDescr/SSH. Only single-platform vendors
# are listed; multi-platform vendors (e.g. cisco → ios/ios_xe/ios_xr/nxos) are
# intentionally absent so the operator picks one.
_VENDOR_DEFAULT_PLATFORM = {
    "fortinet":  "fortios",
    "paloalto":  "panos",
    "arista":    "eos",
    "juniper":   "junos",
    "mikrotik":  "routeros",
    "sonicwall": "sonicwall",
    "aruba":     "aruba",
}


def default_platform_for_vendor(vendor: str) -> str:
    """Default platform for an unambiguous vendor; '' when it needs a choice."""
    return _VENDOR_DEFAULT_PLATFORM.get((vendor or "").lower(), "")


def _vendor_from_services(services: dict[int, dict]) -> str:
    """Vendor hint from nmap -sV product/extrainfo strings (e.g. 'Cisco SSH')."""
    blob = " ".join(
        f"{s.get('product', '')} {s.get('extrainfo', '')}" for s in services.values()
    ).lower()
    for needle, vendor in _BANNER_VENDORS:
        if needle in blob:
            return vendor
    return ""


def _platform_from_banner(banner: str) -> str:
    """
    Best-effort platform from an SSH identification banner, e.g.
    "SSH-2.0-Cisco-1.25" → ios_xe, "SSH-2.0-FortiSSH..." → fortios.
    """
    low = banner.lower()
    if "fortissh" in low or "forti" in low:
        return "fortios"
    if "sonicwall" in low:
        return "sonicwall"
    if "arubaos-cx" in low or "aos-cx" in low:
        return "aos_cx"
    if "aruba" in low:
        return "aruba"
    if "cisco" in low:
        return "ios_xe"   # routers usually IOS-XE; refined at device-add time
    if "arista" in low:
        return "eos"
    return ""


# Common management ports probed for liveness when SNMP is silent.
_PROBE_PORTS = [22, 443, 80, 830, 8443, 23]


def parse_nmap_hosts(xml_data: bytes) -> list[str]:
    """Extract live IPv4 host addresses from `nmap -oX -` output."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []
    hosts: list[str] = []
    for host in root.findall("host"):
        status = host.find("status")
        if status is None or status.get("state") != "up":
            continue
        for addr in host.findall("address"):
            if addr.get("addrtype") == "ipv4":
                hosts.append(addr.get("addr"))
    return hosts


def parse_nmap_services(xml_data: bytes) -> dict[int, dict]:
    """Extract {port: {name, product, version, extrainfo}} for open ports."""
    import xml.etree.ElementTree as ET
    services: dict[int, dict] = {}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return services
    for host in root.findall("host"):
        ports = host.find("ports")
        if ports is None:
            continue
        for port in ports.findall("port"):
            state = port.find("state")
            if state is None or state.get("state") != "open":
                continue
            try:
                portid = int(port.get("portid"))
            except (TypeError, ValueError):
                continue
            svc = port.find("service")
            services[portid] = {
                "name": svc.get("name", "") if svc is not None else "",
                "product": svc.get("product", "") if svc is not None else "",
                "version": svc.get("version", "") if svc is not None else "",
                "extrainfo": svc.get("extrainfo", "") if svc is not None else "",
            }
    return services


def parse_nmap_os(host_xml) -> dict:
    """
    From a single nmap <host> XML element, pick the highest-accuracy <osmatch>
    and return {os_name, os_accuracy, os_family, os_vendor, os_type}. The
    os_family / os_vendor / os_type come from the nested <osclass>. Returns {}
    when there is no OS match.
    """
    os_el = host_xml.find("os")
    if os_el is None:
        return {}
    best = None
    best_acc = -1
    for match in os_el.findall("osmatch"):
        try:
            acc = int(match.get("accuracy", "0"))
        except (TypeError, ValueError):
            acc = 0
        if acc > best_acc:
            best_acc = acc
            best = match
    if best is None:
        return {}
    osclass = best.find("osclass")
    return {
        "os_name": best.get("name", ""),
        "os_accuracy": best_acc,
        "os_family": osclass.get("osfamily", "") if osclass is not None else "",
        "os_vendor": osclass.get("vendor", "") if osclass is not None else "",
        "os_type": osclass.get("type", "") if osclass is not None else "",
    }


# ── Device classification (network device vs endpoint) ────────────────────────
# OS-name substrings that mark a managed network device (matched case-insensitive).
NETWORK_OS_PATTERNS = (
    "cisco ios", "ios-xe", "ios xe", "ios-xr", "ios xr", "nx-os", "nxos", "nexus",
    "junos", "fortios", "fortigate", "arubaos", "arista eos", " eos",
    "sonicos", "pan-os", "extremexos", "vyos", "pfsense", "opnsense",
    "mikrotik", "routeros",
)
# OS-name substrings that mark an endpoint/workstation/server OS.
ENDPOINT_OS_PATTERNS = (
    "windows", "macos", "mac os x", "android", "ipados", "ios ",
    " ios", "ubuntu", "debian", "fedora", "centos", "red hat", "opensuse",
)
# nmap osclass <type> values that imply a network device.
NETWORK_OS_TYPES = {
    "router", "switch", "firewall", "load balancer", "broadband router",
    "wap", "bridge",
}
# nmap osclass <type> values that imply an endpoint/general-purpose host.
ENDPOINT_OS_TYPES = {
    "general purpose", "printer", "phone", "media device", "game console",
    "storage-misc",
}
# Known network-vendor SNMP enterprise prefixes (1.3.6.1.4.1.<N>).
NETWORK_ENTERPRISE_PREFIXES = {
    "9", "2636", "12356", "30065", "14823", "8741", "25461", "14988", "2011",
}


def _enterprise_id(sysobjid: str) -> str:
    prefix = "1.3.6.1.4.1."
    if not sysobjid or not sysobjid.startswith(prefix):
        return ""
    return sysobjid[len(prefix):].split(".", 1)[0]


def classify_device(os_info: dict | None, open_ports, snmp_data) -> tuple[str, str]:
    """
    Classify a probed host as a network device, endpoint, or unknown.

    Returns (category, confidence) where category ∈
    {"network_device", "endpoint", "unknown"} and confidence ∈
    {"high", "medium", "low"}. Pure function — no I/O.

      os_info    : dict from parse_nmap_os (may be {} / None)
      open_ports : iterable of open TCP ports
      snmp_data  : (sysDescr, sysName, sysObjectID) tuple, or falsy
    """
    os_info = os_info or {}
    os_name = (os_info.get("os_name") or "").lower()
    os_type = (os_info.get("os_type") or "").lower()
    ports = set(open_ports or [])
    sysobjid = snmp_data[2] if snmp_data and len(snmp_data) > 2 else ""

    # 1. Network OS name → network device (high).
    if os_name and any(p in os_name for p in NETWORK_OS_PATTERNS):
        return "network_device", "high"

    # 2. SNMP sysObjectID under a known network-vendor enterprise prefix (high).
    if _enterprise_id(sysobjid) in NETWORK_ENTERPRISE_PREFIXES:
        return "network_device", "high"

    # 3. Endpoint OS name → endpoint (high).
    if os_name and any(p in os_name for p in ENDPOINT_OS_PATTERNS):
        return "endpoint", "high"

    # 4. Port heuristics → endpoint (medium).
    if 3389 in ports:                      # RDP
        return "endpoint", "medium"
    if 445 in ports and 139 in ports:      # SMB/NetBIOS
        return "endpoint", "medium"

    # 5. nmap os_type → network device / endpoint (medium).
    if os_type in NETWORK_OS_TYPES:
        return "network_device", "medium"
    if os_type in ENDPOINT_OS_TYPES:
        return "endpoint", "medium"

    return "unknown", "low"


class Command(BaseCommand):
    help = "Run device discovery (scan, topology walk, or resume existing job)"

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, help="Resume an existing DiscoveryJob by ID")
        parser.add_argument("--scan", metavar="CIDR", help="Subnet to active-scan, e.g. 10.0.0.0/24")
        parser.add_argument("--topology", action="store_true", help="Topology walk from seed device")
        parser.add_argument("--seed", metavar="IP", help="Seed IP for topology walk")
        parser.add_argument("--name", default="", help="Job name")
        parser.add_argument("--community", default="public", help="SNMP v2c community string")
        parser.add_argument("--max-depth", type=int, default=10)
        parser.add_argument("--max-devices", type=int, default=1000)
        parser.add_argument("--rate-pps", type=int, default=10, help="Probes per second")
        parser.add_argument(
            "--allowed-subnets", nargs="*", default=[],
            help="Only probe IPs in these CIDRs (safety control)"
        )
        parser.add_argument(
            "--excluded-subnets", nargs="*", default=[],
            help="Never probe these CIDRs (OT/ICS/SCADA protection)"
        )

    def handle(self, *args, **options):
        asyncio.run(self._run(options))

    async def _run(self, options: dict) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )

        job = await self._get_or_create_job(options)
        # Load the SNMP/SSH probe credentials from the job's credential profile
        # (secrets from OpenBao); falls back to the --community flag.
        probe = await asyncio.get_event_loop().run_in_executor(
            None, self._job_probe_config, job, options["community"]
        )
        runner = DiscoveryRunner(
            job=job,
            probe_config=probe,
            rate_pps=job.rate_limit_pps,
        )
        await runner.run()

    @staticmethod
    def _job_probe_config(job: DiscoveryJob, default_community: str) -> dict:
        """
        Resolve SNMP + SSH probe credentials for the job from its credential
        profile (secrets in OpenBao). Returns:
          {snmp_version: 2|3, community, v3: {...}|None, ssh: {...}|None}
        """
        cfg: dict = {"snmp_version": 2, "community": default_community, "v3": None, "ssh": None}
        profile = getattr(job, "credential_profile", None)
        if not profile:
            return cfg
        try:
            from apps.credentials import vault
            secrets = vault.read_secret(profile.vault_path) if profile.vault_path else {}
        except Exception as exc:  # OpenBao down / path missing — fall back safely.
            logger.warning("could not read credentials for job %d: %s", job.id, exc)
            secrets = {}

        if profile.snmpv3_enabled:
            cfg["snmp_version"] = 3
            cfg["v3"] = {
                "username": profile.snmpv3_username or secrets.get("snmpv3_username", ""),
                "auth_key": secrets.get("snmpv3_auth_key", ""),
                "priv_key": secrets.get("snmpv3_priv_key", ""),
                "auth_protocol": (profile.snmpv3_auth_protocol or "SHA").upper(),
                "priv_protocol": (profile.snmpv3_priv_protocol or "AES").upper(),
                "security_level": profile.snmpv3_security_level or "authPriv",
            }
        elif profile.snmpv2c_enabled:
            cfg["community"] = secrets.get("snmpv2c_community") or default_community

        if profile.ssh_enabled:
            cfg["ssh"] = {
                "username": profile.ssh_username or "",
                "password": secrets.get("ssh_password", ""),
                "port": profile.ssh_port or 22,
            }
        return cfg

    async def _get_or_create_job(self, options: dict) -> DiscoveryJob:
        if options["job"]:
            def _fetch():
                return (DiscoveryJob.objects
                        .select_related("credential_profile", "seed_device")
                        .get(id=options["job"]))
            try:
                return await asyncio.get_event_loop().run_in_executor(None, _fetch)
            except DiscoveryJob.DoesNotExist:
                raise CommandError(f"DiscoveryJob {options['job']} not found")

        if options["scan"]:
            method = DiscoveryJob.Method.SCAN
            subnets = [options["scan"]]
            name = options["name"] or f"Scan {options['scan']}"
        elif options["topology"]:
            if not options["seed"]:
                raise CommandError("--seed IP required for topology walk")
            method = DiscoveryJob.Method.TOPOLOGY
            subnets = []
            name = options["name"] or f"Topology walk from {options['seed']}"
        else:
            raise CommandError("Specify --job, --scan CIDR, or --topology --seed IP")

        def _create():
            return DiscoveryJob.objects.create(
                name=name,
                method=method,
                subnets=subnets,
                allowed_subnets=options["allowed_subnets"],
                excluded_subnets=options["excluded_subnets"],
                max_depth=options["max_depth"],
                max_devices=options["max_devices"],
                rate_limit_pps=options["rate_pps"],
            )

        return await asyncio.get_event_loop().run_in_executor(None, _create)


class DiscoveryRunner:
    """Executes a DiscoveryJob — scan or topology walk."""

    def __init__(self, job: DiscoveryJob, community: str = "public", rate_pps: int = 10,
                 probe_config: dict | None = None) -> None:
        self._job       = job
        self._probe_cfg = probe_config or {"snmp_version": 2, "community": community,
                                           "v3": None, "ssh": None}
        self._community = self._probe_cfg.get("community", community)
        self._delay     = 1.0 / max(rate_pps, 1)
        self._seen: set[str] = set()
        self._queue: list[str] = []
        self._found    = 0
        self._cancelled = False
        self._loop     = asyncio.get_event_loop()

        self._allowed: list[ipaddress.IPv4Network] = [
            ipaddress.ip_network(s, strict=False)
            for s in (job.allowed_subnets or [])
        ]
        self._excluded: list[ipaddress.IPv4Network] = [
            ipaddress.ip_network(s, strict=False)
            for s in (job.excluded_subnets or [])
        ]

    async def run(self) -> None:
        # Honour a cancel requested before the engine started (e.g. cancelled
        # while still pending, before this worker thread picked it up).
        if await self._check_cancel():
            await self._finish_cancelled()
            return
        await self._set_status(DiscoveryJob.Status.RUNNING)
        await self._set_progress(message="Initializing scan...", current=0, ips=0)
        try:
            if self._job.method == DiscoveryJob.Method.SCAN:
                await self._active_scan()
            elif self._job.method == DiscoveryJob.Method.PING_SNMP:
                await self._ping_scan(with_snmp=True)
            elif self._job.method == DiscoveryJob.Method.PING:
                await self._ping_scan(with_snmp=False)
            elif self._job.method == DiscoveryJob.Method.TOPOLOGY:
                seed_ip = self._job.subnets[0] if self._job.subnets else None
                if not seed_ip and self._job.seed_device:
                    seed_ip = self._job.seed_device.ip_address
                if seed_ip:
                    self._queue.append(seed_ip)
                    await self._topology_walk()
            if self._cancelled:
                await self._finish_cancelled()
            else:
                await self._set_status(DiscoveryJob.Status.COMPLETED)
                await self._set_progress(message=f"Complete: {self._found} devices found")
        except Exception as exc:
            logger.error("discovery job %d failed: %s", self._job.id, exc)
            await self._set_status(DiscoveryJob.Status.FAILED, str(exc))
            await self._set_progress(message=f"Failed: {exc}")

    async def _check_cancel(self) -> bool:
        """Re-read the cancel_requested flag from the DB (set by the API)."""
        def _db():
            return (DiscoveryJob.objects
                    .filter(pk=self._job.pk)
                    .values_list("cancel_requested", flat=True).first())
        return bool(await self._loop.run_in_executor(None, _db))

    async def _finish_cancelled(self) -> None:
        self._cancelled = True
        await self._set_status(DiscoveryJob.Status.CANCELLED)
        await self._set_progress(message="Cancelled by user")

    # ── active scan ───────────────────────────────────────────────────────────

    @staticmethod
    def _host_count(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
        """Usable host count without materialising the generator."""
        if net.prefixlen >= net.max_prefixlen - 1:  # /31, /32 (and v6 equivalents)
            return net.num_addresses
        return net.num_addresses - 2

    async def _active_scan(self) -> None:
        # Phase 1 — host discovery. Prefer a fast nmap ping sweep; fall back to
        # iterating the whole range when nmap is unavailable.
        candidates: list[str] = []
        used_nmap = True
        for subnet_str in self._job.subnets:
            await self._set_progress(message=f"Running ping sweep on {subnet_str}...")
            live = await self._nmap_live_hosts(subnet_str)
            if live is None:
                used_nmap = False
                net = ipaddress.ip_network(subnet_str, strict=False)
                candidates += [str(h) for h in net.hosts()]
            else:
                logger.info("nmap: %d live host(s) in %s", len(live), subnet_str)
                candidates += [ip for ip in live if ip not in candidates]

        candidates = [ip for ip in candidates if self._is_allowed(ip)]
        total = len(candidates) or 1
        await self._set_progress(
            total=total, current=0, ips=0,
            message=(f"Found {len(candidates)} live host(s), identifying..."
                     if used_nmap else f"Scanning {total} addresses..."))

        # Phase 2 — identify each candidate (SNMP / SSH / TCP).
        scanned = 0
        for ip in candidates:
            if self._found >= self._job.max_devices:
                logger.warning("max_devices %d reached", self._job.max_devices)
                await self._set_progress(
                    current=scanned, ips=scanned,
                    message=f"Stopped at max_devices ({self._job.max_devices})")
                return
            scanned += 1
            if scanned % 10 == 0 and await self._check_cancel():
                self._cancelled = True
                return
            # Save progress every host for short (nmap) lists, else every 10.
            if total <= 64 or scanned % 10 == 0:
                await self._set_progress(
                    current=scanned, ips=scanned,
                    message=f"Identifying {ip}... ({scanned}/{total})")
            await self._probe(ip, depth=0)
            await asyncio.sleep(self._delay)
        await self._set_progress(
            current=scanned, total=total, ips=scanned, message="Processing results...")

    # ── ping-based scans (production-safe: ICMP only, no nmap/port scan) ────────

    @staticmethod
    def _expand_hosts(subnet: str) -> list[str]:
        """Usable host IPs in a CIDR (the whole range for /31 and /32)."""
        try:
            net = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            return []
        if net.prefixlen >= net.max_prefixlen - 1:  # /31, /32 (+ v6 equivalents)
            return [str(h) for h in net]
        return [str(h) for h in net.hosts()]

    async def _ping_scan(self, with_snmp: bool) -> None:
        """
        Production-safe discovery: ICMP ping sweep of the job's subnets, then —
        for ``ping_snmp`` only — SNMP fingerprinting + an SSH banner read of each
        live host. No nmap, no port scanning, no service detection, so it won't
        trip IDS/firewall rules the way the active (nmap) scan can.

        ``with_snmp=False`` is the ``ping`` method: liveness only, devices land
        with platform unknown for manual selection at approval.
        """
        candidates = [ip for sub in self._job.subnets for ip in self._expand_hosts(sub)]
        candidates = [ip for ip in candidates if self._is_allowed(ip)]
        total = len(candidates) or 1
        mode = "Ping + SNMP" if with_snmp else "Ping"
        await self._set_progress(
            total=total, current=0, ips=0,
            message=f"{mode} sweep of {total} address{'es' if total != 1 else ''}...")

        scanned = 0
        for ip in candidates:
            if self._found >= self._job.max_devices:
                logger.warning("max_devices %d reached", self._job.max_devices)
                await self._set_progress(
                    current=scanned, ips=scanned,
                    message=f"Stopped at max_devices ({self._job.max_devices})")
                return
            scanned += 1
            if scanned % 10 == 0 and await self._check_cancel():
                self._cancelled = True
                return
            if total <= 64 or scanned % 10 == 0:
                await self._set_progress(
                    current=scanned, ips=scanned,
                    message=f"Pinging {ip}... ({scanned}/{total})")
            if await self._ping_host(ip):
                await self._probe_ping(ip, with_snmp=with_snmp)
            await asyncio.sleep(self._delay)  # rate_limit_pps
        await self._set_progress(
            current=scanned, total=total, ips=scanned, message="Processing results...")

    async def _ping_host(self, ip: str) -> bool:
        """
        ICMP liveness via the system ``ping`` (one echo, 2s wait) — no nmap, no
        port scan. Falls back to icmplib when the ``ping`` binary is absent.
        """
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "2", str(ip),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.communicate(), timeout=3)
            return proc.returncode == 0
        except FileNotFoundError:
            return await self._icmp_ping(ip)
        except Exception:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return False

    async def _probe_ping(self, ip: str, with_snmp: bool) -> None:
        """
        Persist a live host found by the ping sweep. For ``ping_snmp`` also tries
        SNMP (sysDescr/sysName/sysObjectID → vendor/platform) and a non-intrusive
        SSH banner read; for ``ping`` it records ICMP-only (platform unknown).
        """
        self._seen.add(ip)
        methods: list[str] = ["icmp"]
        responds_to: dict = {"icmp": True}
        vendor = hostname = descr = platform = os_version = model = ""
        confidence = 10
        snmp = None
        category = DiscoveredDevice.Category.UNKNOWN

        if with_snmp:
            snmp = await self._snmp_probe(ip)
            if snmp:
                descr, hostname, sysobjid = snmp
                vendor = _vendor_from_sysobjid(sysobjid) or _vendor_from_descr(descr) or ""
                platform = _platform_from_descr(descr) or ""
                methods.append("snmp"); responds_to["snmp"] = True
                confidence = max(confidence, 60)
            banner = await self._ssh_banner(ip)
            if banner:
                methods.append("ssh"); responds_to["ssh"] = True
                vendor = vendor or _vendor_from_banner(banner)
                platform = platform or _platform_from_banner(banner)
                confidence = max(confidence, 40 if vendor else 30)
                if not descr:
                    descr = banner
            # Known vendor but no platform → vendor default (cisco stays blank
            # for the operator to pick at approval).
            if vendor and not platform:
                platform = default_platform_for_vendor(vendor)
            category, _conf = classify_device({}, [], snmp)

        await self._save_discovered(ip, {
            "detection_methods": methods,
            "responds_to": responds_to,
            "confidence_score": confidence,
            "discovered_hostname": hostname,
            "discovered_vendor": vendor,
            "discovered_platform": platform,
            "discovered_os": os_version,
            "discovered_model": model,
            "raw_fingerprint": descr[:500],
            "device_category": category,
        })
        self._found += 1
        await self._update_count(self._found)
        logger.info(
            "found (ping%s): %s  score=%d  vendor=%s  platform=%s  name=%s",
            "+snmp" if with_snmp else "", ip, confidence, vendor or "?",
            platform or "?", hostname or "?",
        )

    async def _nmap_live_hosts(self, subnet: str) -> list[str] | None:
        """
        Fast host discovery via `nmap -sn` (TCP-SYN ping to common mgmt ports so
        SSH-only devices are found even unprivileged). Returns the list of live
        IPs, or None when nmap is unavailable/failed so the caller falls back to
        a full range sweep.
        """
        cmd = [
            "nmap", "-sn", "-n", "-T4", "--min-rate", "100",
            "-PS22,80,443,830,8443", "-oX", "-", subnet,
        ]
        for excl in (self._job.excluded_subnets or []):
            cmd += ["--exclude", excl]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except FileNotFoundError:
            logger.info("nmap not installed — falling back to full range sweep")
            return None
        except Exception as exc:
            logger.warning("nmap host discovery failed (%s) — falling back", exc)
            return None
        if proc.returncode != 0:
            return None
        return parse_nmap_hosts(stdout)

    async def _nmap_services(self, ip: str) -> tuple[dict[int, dict], dict] | None:
        """
        nmap service/version + OS scan of a single host
        (`-sV -O --osscan-guess --open`; OS detection needs NET_RAW, which the
        discovery container has). Returns (services, os_info) where services is
        {port: {name, product, version, extrainfo}} and os_info is the
        parse_nmap_os dict (may be {}). Returns None when nmap is
        unavailable/failed (caller falls back to a plain TCP scan).
        """
        cmd = [
            "nmap", "-sV", "-O", "--osscan-guess", "-n", "-T4", "--open",
            "-p", "22,23,80,161,443,830,8080,8443", "-oX", "-", ip,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("nmap service scan of %s failed (%s)", ip, exc)
            return None
        if proc.returncode != 0:
            return None
        services = parse_nmap_services(stdout)
        os_info = self._parse_first_host_os(stdout)
        return services, os_info

    @staticmethod
    def _parse_first_host_os(xml_data: bytes) -> dict:
        """parse_nmap_os against the first <host> in single-host scan output."""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError:
            return {}
        host = root.find("host")
        return parse_nmap_os(host) if host is not None else {}

    # ── topology walk ─────────────────────────────────────────────────────────

    async def _topology_walk(self) -> None:
        depth = 0
        scanned = 0
        while self._queue and depth <= self._job.max_depth:
            next_layer: list[str] = []
            for ip in self._queue:
                if self._found >= self._job.max_devices:
                    return
                if self._cancelled or await self._check_cancel():
                    self._cancelled = True
                    return
                if ip in self._seen:
                    continue
                if not self._is_allowed(ip):
                    continue
                scanned += 1
                # Topology walk has no fixed total; report progress against the
                # current frontier so the bar still advances.
                await self._set_progress(
                    current=scanned, total=scanned + len(self._queue),
                    ips=scanned, message=f"Walking {ip} (depth {depth})...")
                result = await self._probe(ip, depth=depth)
                await asyncio.sleep(self._delay)
                if result:
                    # Fetch neighbors via SNMP route table
                    neighbors = await self._snmp_next_hops(ip)
                    next_layer.extend(n for n in neighbors if n not in self._seen)
            self._queue = next_layer
            depth += 1

    # ── probing ───────────────────────────────────────────────────────────────

    async def _probe(self, ip: str, depth: int) -> bool:
        """
        Multi-method probe of a single IP. Detection priority:
          1. ICMP ping            (presence)
          2. SNMP sysDescr        (platform id — v2c or v3 from the profile)
          3. TCP management ports (presence when SNMP/ICMP silent)
          4. SSH banner           (vendor hint)
        A device is "discovered" if ANY method responds.
        """
        self._seen.add(ip)

        # Run liveness/identity checks concurrently to keep per-IP time low.
        # Port scan: nmap -sV (service/version) when available, else a plain TCP
        # connect scan.
        ping_ok, snmp, nmap_result = await asyncio.gather(
            self._icmp_ping(ip),
            self._snmp_probe(ip),
            self._nmap_services(ip),
        )
        os_info: dict = {}
        if nmap_result is None:
            open_ports = await self._tcp_scan(ip, _PROBE_PORTS)
            services = {}
        else:
            services, os_info = nmap_result
            open_ports = sorted(services.keys())
        ssh_banner = await self._ssh_banner(ip) if 22 in open_ports else ""

        logger.debug(
            "Probing %s: ping=%s snmp=%s ports=%s ssh=%s",
            ip, ping_ok, bool(snmp), open_ports, bool(ssh_banner),
        )

        if not (ping_ok or snmp or open_ports):
            return False

        methods: list[str] = []
        responds_to: dict = {}
        vendor = hostname = descr = platform = os_version = model = ""
        confidence = 0

        if ping_ok:
            methods.append("icmp"); responds_to["icmp"] = True
            confidence = max(confidence, 10)
        if snmp:
            descr, hostname, sysobjid = snmp
            # sysObjectID enterprise OID is the most reliable vendor signal.
            vendor = _vendor_from_sysobjid(sysobjid) or _vendor_from_descr(descr) or vendor
            platform = _platform_from_descr(descr) or platform
            methods.append("snmp"); responds_to["snmp"] = True
            confidence = max(confidence, 60)
        if open_ports:
            responds_to["tcp"] = True
            methods += [f"tcp/{p}" for p in open_ports]
            confidence = max(confidence, 20)
            if 443 in open_ports or 80 in open_ports or 8443 in open_ports:
                responds_to["http"] = True
            if 830 in open_ports:  # NETCONF → managed network device
                responds_to["netconf"] = True
            # nmap -sV product/extrainfo can name the vendor (e.g. Cisco SSH).
            if not vendor:
                vendor = _vendor_from_services(services)
        if ssh_banner:
            methods.append("ssh"); responds_to["ssh"] = True
            vendor = vendor or _vendor_from_banner(ssh_banner)
            platform = platform or _platform_from_banner(ssh_banner)
            if vendor:
                confidence = max(confidence, 40)
            else:
                confidence = max(confidence, 30)

        # Deepest identification: SSH login + show-version, only when SNMP didn't
        # already identify the platform, port 22 is open, and the job carries SSH
        # creds. Best-effort and time-boxed (Netmiko is slow).
        if 22 in open_ports and not (snmp and platform) and self._probe_cfg.get("ssh"):
            det = await self._ssh_identify(ip)
            if det:
                vendor = det.get("vendor") or vendor
                platform = det.get("platform") or platform
                os_version = det.get("os_version") or os_version
                model = det.get("model") or model
                hostname = det.get("hostname") or hostname
                if "ssh_login" not in methods:
                    methods.append("ssh_login")
                confidence = max(confidence, 80)

        # Known vendor but platform still unidentified → fall back to the
        # vendor's default platform (fortinet → fortios, etc.). Multi-platform
        # vendors (cisco) stay blank for the operator to pick at approval.
        if vendor and not platform:
            platform = default_platform_for_vendor(vendor)

        # Classify (network device vs endpoint) — independent of confidence_score.
        category, _cat_conf = classify_device(os_info, open_ports, snmp)

        await self._save_discovered(ip, {
            "detection_methods": methods,
            "responds_to": responds_to,
            "confidence_score": confidence,
            "discovered_hostname": hostname,
            "discovered_vendor": vendor,
            "discovered_platform": platform,
            "discovered_os": os_version,
            "discovered_model": model,
            "raw_fingerprint": (descr or ssh_banner)[:500],
            "device_category": category,
            "os_detected": os_info.get("os_name", ""),
            "os_accuracy": os_info.get("os_accuracy"),
        })
        self._found += 1
        await self._update_count(self._found)
        logger.info(
            "found: %s  score=%d  vendor=%s  platform=%s  methods=%s  name=%s",
            ip, confidence, vendor or "?", platform or "?", ",".join(methods), hostname or "?",
        )
        return True

    # ── probe methods ─────────────────────────────────────────────────────────

    async def _icmp_ping(self, ip: str) -> bool:
        """Best-effort ICMP ping (unprivileged). False on any error/timeout."""
        try:
            from icmplib import async_ping
            host = await async_ping(ip, count=1, timeout=1, privileged=False)
            return bool(host.is_alive)
        except Exception:  # no NET_RAW / unsupported / unreachable
            return False

    async def _tcp_scan(self, ip: str, ports: list[int]) -> list[int]:
        """Return the subset of ports that accept a TCP connection (concurrently)."""
        async def _one(port: int) -> int | None:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), timeout=1.5)
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass
                return port
            except (asyncio.TimeoutError, OSError, ConnectionError):
                return None
        results = await asyncio.gather(*(_one(p) for p in ports))
        return [p for p in results if p is not None]

    async def _ssh_banner(self, ip: str) -> str:
        """Read the SSH identification banner from port 22 (no login)."""
        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, 22), timeout=1.5)
            line = await asyncio.wait_for(reader.readline(), timeout=1.5)
            return line.decode(errors="replace").strip()
        except (asyncio.TimeoutError, OSError, ConnectionError):
            return ""
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

    async def _ssh_identify(self, ip: str) -> dict | None:
        """
        Best-effort SSH login + show-version via Netmiko (apps.devices.detect),
        using the job's SSH credentials. Time-boxed; returns the detect result
        dict or None. Runs in an executor (Netmiko is blocking/slow).
        """
        ssh = self._probe_cfg.get("ssh") or {}
        if not ssh.get("username"):
            return None

        def _detect():
            from apps.devices import detect
            return detect.detect_platform(
                ip, ssh["username"], ssh.get("password", ""), ssh.get("port", 22))
        try:
            det = await asyncio.wait_for(
                self._loop.run_in_executor(None, _detect), timeout=20)
        except Exception:
            return None
        return det if det and det.get("detected") else None

    async def _snmp_probe(self, ip: str) -> tuple[str, str, str] | None:
        """
        SNMP GET sysDescr+sysName+sysObjectID via pysnmp using the job's
        credentials. Tries the profile auth first (SNMPv3 or its v2c community),
        then falls back to the v2c "public" community. Returns
        (sysDescr, sysName, sysObjectID) or None.
        """
        try:
            from pysnmp.hlapi.v3arch.asyncio import (
                CommunityData, ContextData, ObjectIdentity, ObjectType,
                SnmpEngine, UdpTransportTarget, UsmUserData, get_cmd,
            )
        except Exception:
            return None

        # Auth candidates in priority order (dedupe a redundant public fallback).
        candidates = [self._snmp_auth(UsmUserData, CommunityData)]
        if self._community != "public":
            candidates.append(CommunityData("public", mpModel=1))

        for auth_data in candidates:
            if auth_data is None:
                continue
            try:
                target = await UdpTransportTarget.create((ip, 161), timeout=1.5, retries=0)
                err_ind, err_stat, _err_idx, var_binds = await get_cmd(
                    SnmpEngine(), auth_data, target, ContextData(),
                    ObjectType(ObjectIdentity(_OID_SYS_DESCR)),
                    ObjectType(ObjectIdentity(_OID_SYS_NAME)),
                    ObjectType(ObjectIdentity(_OID_SYS_OBJID)),
                )
                if err_ind or err_stat:
                    continue
                vals = [str(vb[1]) for vb in var_binds]
                descr = vals[0] if len(vals) > 0 else ""
                name = vals[1] if len(vals) > 1 else ""
                sysobjid = vals[2] if len(vals) > 2 else ""
                if descr or name:
                    return descr, name, sysobjid
            except Exception:
                continue
        return None

    def _snmp_auth(self, UsmUserData, CommunityData):
        """Build the pysnmp auth object from the resolved probe credentials."""
        if self._probe_cfg.get("snmp_version") == 3 and self._probe_cfg.get("v3"):
            v3 = self._probe_cfg["v3"]
            if not v3.get("username"):
                return None
            try:
                from pysnmp.hlapi.v3arch.asyncio import (
                    usmAesCfb128Protocol, usmAesCfb192Protocol, usmAesCfb256Protocol,
                    usmDESPrivProtocol, usmHMAC128SHA224AuthProtocol,
                    usmHMAC192SHA256AuthProtocol, usmHMAC256SHA384AuthProtocol,
                    usmHMAC384SHA512AuthProtocol, usmHMACMD5AuthProtocol,
                    usmHMACSHAAuthProtocol,
                )
            except Exception:
                return None
            auth_map = {
                "MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol,
                "SHA224": usmHMAC128SHA224AuthProtocol, "SHA256": usmHMAC192SHA256AuthProtocol,
                "SHA384": usmHMAC256SHA384AuthProtocol, "SHA512": usmHMAC384SHA512AuthProtocol,
            }
            priv_map = {
                "DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol,
                "AES128": usmAesCfb128Protocol, "AES192": usmAesCfb192Protocol,
                "AES256": usmAesCfb256Protocol,
            }
            auth_p = auth_map.get(v3["auth_protocol"], usmHMACSHAAuthProtocol)
            priv_p = priv_map.get(v3["priv_protocol"], usmAesCfb128Protocol)
            level = v3.get("security_level", "authPriv")
            if level == "noAuthNoPriv":
                return UsmUserData(v3["username"])
            if level == "authNoPriv" or not v3.get("priv_key"):
                return UsmUserData(v3["username"], v3.get("auth_key") or None, authProtocol=auth_p)
            return UsmUserData(v3["username"], v3["auth_key"], v3["priv_key"],
                               authProtocol=auth_p, privProtocol=priv_p)
        # SNMPv2c (mpModel=1).
        return CommunityData(self._community, mpModel=1)


    async def _snmp_next_hops(self, ip: str) -> list[str]:
        """Extract next-hop IPs from the IP route table via SNMP walk (simplified)."""
        # In production this would do a full SNMP walk of ipCidrRouteTable.
        # For now return empty — the full implementation lives in ingest-snmp.
        return []

    # ── safety ────────────────────────────────────────────────────────────────

    def _is_allowed(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for excl in self._excluded:
            if addr in excl:
                logger.debug("skipping excluded IP %s (matches %s)", ip, excl)
                return False
        if not self._allowed:
            return True
        for allow in self._allowed:
            if addr in allow:
                return True
        return False

    # ── persistence ───────────────────────────────────────────────────────────

    async def _save_discovered(self, ip: str, data: dict) -> None:
        def _db():
            DiscoveredDevice.objects.update_or_create(
                job=self._job, source_ip=ip,
                defaults={
                    "detection_methods": data.get("detection_methods", []),
                    "responds_to":       data.get("responds_to", {}),
                    "confidence_score":  data.get("confidence_score", 0),
                    "discovered_hostname": data.get("discovered_hostname", ""),
                    "discovered_vendor":   data.get("discovered_vendor", ""),
                    "discovered_platform": data.get("discovered_platform", ""),
                    "discovered_os":       data.get("discovered_os", ""),
                    "discovered_model":    data.get("discovered_model", ""),
                    "raw_fingerprint":     data.get("raw_fingerprint", ""),
                    "device_category":     data.get("device_category",
                                                    DiscoveredDevice.Category.UNKNOWN),
                    "os_detected":         data.get("os_detected", ""),
                    "os_accuracy":         data.get("os_accuracy"),
                    "status": DiscoveredDevice.Status.PENDING,
                },
            )
        await self._loop.run_in_executor(None, _db)

    async def _set_status(self, status: str, error: str = "") -> None:
        def _db():
            updates: dict = {"status": status}
            if status == DiscoveryJob.Status.RUNNING:
                updates["started_at"] = dj_tz.now()
            elif status in (DiscoveryJob.Status.COMPLETED, DiscoveryJob.Status.FAILED,
                            DiscoveryJob.Status.CANCELLED):
                updates["completed_at"] = dj_tz.now()
            if error:
                updates["error_message"] = error
            DiscoveryJob.objects.filter(pk=self._job.pk).update(**updates)
        await self._loop.run_in_executor(None, _db)

    async def _update_count(self, count: int) -> None:
        def _db():
            DiscoveryJob.objects.filter(pk=self._job.pk).update(devices_found=count)
        await self._loop.run_in_executor(None, _db)

    async def _set_progress(self, *, current=None, total=None, message=None, ips=None) -> None:
        def _db():
            updates: dict = {}
            if current is not None:
                updates["progress_current"] = current
            if total is not None:
                updates["progress_total"] = total
            if message is not None:
                updates["progress_message"] = message[:255]
            if ips is not None:
                updates["ips_scanned"] = ips
            if updates:
                DiscoveryJob.objects.filter(pk=self._job.pk).update(**updates)
        await self._loop.run_in_executor(None, _db)

    async def _maybe_progress(self, scanned: int, total: int, message: str) -> None:
        """Persist progress every 10 IPs (and the message) to avoid DB spam."""
        if scanned % 10 == 0:
            await self._set_progress(current=scanned, ips=scanned, message=message)
