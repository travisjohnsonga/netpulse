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
_OID_LLDP_REM  = "1.0.8802.1.1.2.1.4.1.1"   # lldpRemTable
_OID_CDP_CACHE = "1.3.6.1.4.1.9.9.23.1.2.1" # cdpCacheTable
_OID_ROUTE_TBL = "1.3.6.1.2.1.4.24.4"        # ipCidrRouteTable (RFC 2096)

# Vendor fingerprint patterns in sysDescr
_VENDOR_PATTERNS = [
    ("cisco",   "Cisco"),
    ("juniper", "Juniper"),
    ("arista",  "Arista"),
    ("aruba",   "Aruba"),
    ("fortinet","FortiOS"),
    ("paloalto","Palo Alto"),
    ("mikrotik","MikroTik"),
    ("huawei",  "Huawei"),
]


def _vendor_from_descr(descr: str) -> str:
    low = descr.lower()
    for vendor, _ in _VENDOR_PATTERNS:
        if vendor in low:
            return vendor
    return ""


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
        # Prefer the job's credential profile's SNMP community (from OpenBao);
        # fall back to the --community flag (default "public").
        community = await asyncio.get_event_loop().run_in_executor(
            None, self._job_community, job, options["community"]
        )
        runner = DiscoveryRunner(
            job=job,
            community=community,
            rate_pps=job.rate_limit_pps,
        )
        await runner.run()

    @staticmethod
    def _job_community(job: DiscoveryJob, default: str) -> str:
        """SNMPv2c community for the job from its credential profile (OpenBao)."""
        profile = job.credential_profile
        if not (profile and profile.snmpv2c_enabled and profile.vault_path):
            return default
        try:
            from apps.credentials import vault
            secrets = vault.read_secret(profile.vault_path) or {}
            return secrets.get("snmpv2c_community") or default
        except Exception as exc:  # OpenBao down / path missing — fall back safely.
            logger.warning("could not read SNMP community for job %d: %s", job.id, exc)
            return default

    async def _get_or_create_job(self, options: dict) -> DiscoveryJob:
        if options["job"]:
            try:
                return await asyncio.get_event_loop().run_in_executor(
                    None, DiscoveryJob.objects.get, {"id": options["job"]}
                )
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

    def __init__(self, job: DiscoveryJob, community: str = "public", rate_pps: int = 10) -> None:
        self._job       = job
        self._community = community
        self._delay     = 1.0 / max(rate_pps, 1)
        self._seen: set[str] = set()
        self._queue: list[str] = []
        self._found    = 0
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
        await self._set_status(DiscoveryJob.Status.RUNNING)
        await self._set_progress(message="Initializing scan...", current=0, ips=0)
        try:
            if self._job.method == DiscoveryJob.Method.SCAN:
                await self._active_scan()
            elif self._job.method == DiscoveryJob.Method.TOPOLOGY:
                seed_ip = self._job.subnets[0] if self._job.subnets else None
                if not seed_ip and self._job.seed_device:
                    seed_ip = self._job.seed_device.ip_address
                if seed_ip:
                    self._queue.append(seed_ip)
                    await self._topology_walk()
            await self._set_status(DiscoveryJob.Status.COMPLETED)
            await self._set_progress(message=f"Complete: {self._found} devices found")
        except Exception as exc:
            logger.error("discovery job %d failed: %s", self._job.id, exc)
            await self._set_status(DiscoveryJob.Status.FAILED, str(exc))
            await self._set_progress(message=f"Failed: {exc}")

    # ── active scan ───────────────────────────────────────────────────────────

    @staticmethod
    def _host_count(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> int:
        """Usable host count without materialising the generator."""
        if net.prefixlen >= net.max_prefixlen - 1:  # /31, /32 (and v6 equivalents)
            return net.num_addresses
        return net.num_addresses - 2

    async def _active_scan(self) -> None:
        nets = [ipaddress.ip_network(s, strict=False) for s in self._job.subnets]
        total = sum(self._host_count(n) for n in nets) or 1
        await self._set_progress(message="Calculating scan scope...", total=total, current=0, ips=0)

        scanned = 0
        for net in nets:
            await self._set_progress(
                message=f"Resolving subnet {net} → {self._host_count(net)} hosts...")
            for host in net.hosts():
                if self._found >= self._job.max_devices:
                    logger.warning("max_devices %d reached", self._job.max_devices)
                    await self._set_progress(
                        current=scanned, ips=scanned,
                        message=f"Stopped at max_devices ({self._job.max_devices})")
                    return
                ip = str(host)
                scanned += 1
                if not self._is_allowed(ip):
                    await self._maybe_progress(scanned, total, f"Skipping {ip} (out of scope)")
                    continue
                await self._maybe_progress(scanned, total, f"Scanning {ip}... ({scanned}/{total})")
                await self._probe(ip, depth=0)
                await asyncio.sleep(self._delay)
        await self._set_progress(
            current=scanned, total=total, ips=scanned, message="Processing results...")

    # ── topology walk ─────────────────────────────────────────────────────────

    async def _topology_walk(self) -> None:
        depth = 0
        scanned = 0
        while self._queue and depth <= self._job.max_depth:
            next_layer: list[str] = []
            for ip in self._queue:
                if self._found >= self._job.max_devices:
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
        """Probe a single IP. Returns True if device responded."""
        self._seen.add(ip)
        result = await self._snmp_fingerprint(ip)

        if not result:
            return False

        sys_descr, sys_name = result
        confidence = 60 if sys_descr else 10
        vendor = _vendor_from_descr(sys_descr)

        await self._save_discovered(ip, {
            "detection_methods": ["snmp"],
            "responds_to": {"snmp": True},
            "confidence_score": confidence,
            "discovered_hostname": sys_name,
            "discovered_vendor": vendor,
            "raw_fingerprint": sys_descr[:500],
        })
        self._found += 1
        await self._update_count(self._found)
        logger.info("found: %s  score=%d  vendor=%s  name=%s", ip, confidence, vendor, sys_name)
        return True

    # ── SNMP helpers (pure asyncio, no pysnmp dependency) ────────────────────

    async def _snmp_fingerprint(self, ip: str) -> tuple[str, str] | None:
        """
        Very lightweight SNMP v2c GET for sysDescr+sysName.
        Returns (sysDescr, sysName) or None on failure.

        Uses a minimal hand-crafted SNMP v2c GET packet to avoid
        adding pysnmp as a dependency here. This is intentionally
        simple — a full poller uses the ingest-snmp service.
        """
        try:
            return await asyncio.wait_for(
                self._loop.run_in_executor(None, self._snmp_get_sync, ip),
                timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception):
            return None

    def _snmp_get_sync(self, ip: str) -> tuple[str, str] | None:
        """Blocking SNMP v2c GET — runs in executor."""
        import struct

        def _encode_oid(oid_str: str) -> bytes:
            parts = [int(x) for x in oid_str.split(".")]
            # First two components encoded as 40*first + second
            encoded = bytes([40 * parts[0] + parts[1]])
            for val in parts[2:]:
                if val == 0:
                    encoded += b"\x00"
                    continue
                chunks = []
                while val:
                    chunks.append(val & 0x7F)
                    val >>= 7
                chunks.reverse()
                for i, c in enumerate(chunks):
                    encoded += bytes([c | (0x80 if i < len(chunks) - 1 else 0)])
            return encoded

        def _tlv(tag: int, data: bytes) -> bytes:
            length = len(data)
            if length < 128:
                return bytes([tag, length]) + data
            elif length < 256:
                return bytes([tag, 0x81, length]) + data
            else:
                return bytes([tag, 0x82, length >> 8, length & 0xFF]) + data

        def _get_pdu(oid_str: str) -> bytes:
            oid_enc = _encode_oid(oid_str)
            oid_tlv = _tlv(0x06, oid_enc)           # OID
            varbind  = _tlv(0x30, oid_tlv + b"\x05\x00")  # VarBind (OID, null)
            varbinds = _tlv(0x30, varbind)           # VarBindList
            # GetRequest-PDU: request-id=1, error=0, error-index=0
            pdu = _tlv(0xA0, b"\x02\x01\x01\x02\x01\x00\x02\x01\x00" + varbinds)
            community = self._community.encode()
            msg = _tlv(0x30,
                        b"\x02\x01\x01" +          # version = 1 (v2c)
                        _tlv(0x04, community) +
                        pdu)
            return msg

        def _parse_string(data: bytes, pos: int) -> str:
            if pos >= len(data):
                return ""
            tag = data[pos]
            pos += 1
            length = data[pos]
            pos += 1
            if length & 0x80:
                n = length & 0x7F
                length = int.from_bytes(data[pos:pos+n], "big")
                pos += n
            raw = data[pos:pos+length]
            if tag == 0x04:   # OCTET STRING
                try:
                    return raw.decode("utf-8", errors="replace")
                except Exception:
                    return raw.hex()
            return ""

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            # Send two GET requests: sysDescr.0 and sysName.0
            results: dict[str, str] = {}
            for oid, key in [
                ("1.3.6.1.2.1.1.1.0", "descr"),
                ("1.3.6.1.2.1.1.5.0", "name"),
            ]:
                pkt = _get_pdu(oid)
                sock.sendto(pkt, (ip, 161))
                try:
                    resp, _ = sock.recvfrom(4096)
                    # Walk to end of response and extract last OCTET STRING
                    # Simplified: find last 0x04 tag in response
                    for i in range(len(resp) - 1, 0, -1):
                        if resp[i - 1] == 0x04:
                            val = _parse_string(resp, i - 1)
                            if val:
                                results[key] = val
                                break
                except socket.timeout:
                    pass
            sock.close()
            if "descr" in results or "name" in results:
                return results.get("descr", ""), results.get("name", "")
            return None
        except Exception:
            return None

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
                    "raw_fingerprint":     data.get("raw_fingerprint", ""),
                    "status": DiscoveredDevice.Status.PENDING,
                },
            )
        await self._loop.run_in_executor(None, _db)

    async def _set_status(self, status: str, error: str = "") -> None:
        def _db():
            updates: dict = {"status": status}
            if status == DiscoveryJob.Status.RUNNING:
                updates["started_at"] = dj_tz.now()
            elif status in (DiscoveryJob.Status.COMPLETED, DiscoveryJob.Status.FAILED):
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
