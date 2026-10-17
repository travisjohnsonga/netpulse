"""REST endpoints for ARP/MAC tables, global IP/MAC search and OUI lookup."""
from __future__ import annotations

import ipaddress
import logging
import re
import threading
from math import ceil

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasCapability
from apps.devices.models import Device
from .models import ARPEntry, MACEntry, MACVendor
from .normalize import normalize_mac, oui_of

logger = logging.getLogger(__name__)


def _vendor_map(macs) -> dict:
    """Resolve {mac: vendor} for a set of MACs via their OUIs in one query."""
    ouis = {oui_of(m) for m in macs if m}
    ouis.discard("")
    if not ouis:
        return {}
    lookup = dict(MACVendor.objects.filter(oui__in=ouis).values_list("oui", "vendor"))
    return {m: lookup.get(oui_of(m), "") for m in macs if m}


def _arp_dict(e: ARPEntry, vendors: dict) -> dict:
    return {
        "id": e.id, "ip_address": e.ip_address, "mac_address": e.mac_address,
        "vendor": vendors.get(e.mac_address, ""), "interface": e.interface,
        "vlan": e.vlan, "protocol": e.protocol, "entry_type": e.entry_type,
        "age_minutes": e.age_minutes, "collected_at": e.collected_at.isoformat(),
    }


def _mac_dict(e: MACEntry, vendors: dict) -> dict:
    return {
        "id": e.id, "mac_address": e.mac_address, "vendor": vendors.get(e.mac_address, ""),
        "vlan": e.vlan, "interface": e.interface, "entry_type": e.entry_type,
        "collected_at": e.collected_at.isoformat(),
    }


class DeviceARPView(APIView):
    permission_classes = [HasCapability("device:view")]

    def get(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        qs = ARPEntry.objects.filter(device=device)
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(Q(ip_address__icontains=search) | Q(mac_address__icontains=search))
        qs = qs.order_by("ip_address")
        vendors = _vendor_map([e.mac_address for e in qs])
        last = qs.order_by("-collected_at").values_list("collected_at", flat=True).first()
        return Response({
            "count": qs.count(),
            "last_collected": last.isoformat() if last else None,
            "results": [_arp_dict(e, vendors) for e in qs],
        })


class DeviceMACView(APIView):
    permission_classes = [HasCapability("device:view")]

    def get(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        qs = MACEntry.objects.filter(device=device)
        vlan = request.query_params.get("vlan")
        if vlan:
            qs = qs.filter(vlan=vlan)
        interface = request.query_params.get("interface")
        if interface:
            qs = qs.filter(interface__icontains=interface)
        search = request.query_params.get("search")
        if search:
            qs = qs.filter(mac_address__icontains=search)
        qs = qs.order_by("vlan", "mac_address")
        vendors = _vendor_map([e.mac_address for e in qs])
        last = qs.order_by("-collected_at").values_list("collected_at", flat=True).first()
        return Response({
            "count": qs.count(),
            "last_collected": last.isoformat() if last else None,
            "results": [_mac_dict(e, vendors) for e in qs],
        })


def _run_arp_collection(device, secrets: dict, username: str) -> None:
    """Background worker: collect ARP/MAC over SSH and persist the rows.

    Runs in a daemon thread so the request returns immediately — SonicOS and
    other slow-CLI devices can take well past the gunicorn worker timeout to
    walk their ARP cache.
    """
    from .collector import collect_arp_mac, store_arp_mac
    try:
        arp, mac = collect_arp_mac(device, secrets, username)
        n_arp, n_mac = store_arp_mac(device, arp, mac)
        logger.info("arp_mac: collected %s ARP / %s MAC entries for %s",
                    n_arp, n_mac, device.hostname)
    except Exception:
        logger.exception("arp_mac: background collection failed for %s", device.hostname)


class DeviceARPMACCollectView(APIView):
    """Trigger an immediate ARP/MAC collection for one device (runs async)."""
    permission_classes = [HasCapability("device:edit")]

    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        from apps.compliance.collector import get_credentials
        from .collector import DEVICE_TYPE_MAP

        if (device.platform or "").lower() not in DEVICE_TYPE_MAP:
            return Response({"error": f"ARP/MAC collection not supported for platform '{device.platform}'."},
                            status=status.HTTP_400_BAD_REQUEST)
        profile = device.credential_profile
        username = profile.ssh_username if profile else ""
        secrets = get_credentials(device)
        if not username or not secrets.get("ssh_password"):
            return Response({"error": "No SSH credentials configured for this device."},
                            status=status.HTTP_400_BAD_REQUEST)
        # Collection runs in the background (slow devices exceed the gunicorn
        # worker timeout); mirror the config-backup / CVE-sync async pattern.
        threading.Thread(
            target=_run_arp_collection,
            args=(device, secrets, username),
            name=f"arp-collect-{device_id}",
            daemon=True,
        ).start()
        return Response({"status": "started", "device_id": device_id},
                        status=status.HTTP_202_ACCEPTED)


class NetworkSearchView(APIView):
    """Find which device(s) see a given IP or MAC — 'where is this host?'."""
    permission_classes = [HasCapability("device:view")]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if not q:
            return Response({"query": "", "arp": [], "mac": []})
        mac_norm = normalize_mac(q)
        arp = (ARPEntry.objects
               .filter(Q(ip_address__icontains=q) | Q(mac_address__icontains=mac_norm) | Q(mac_address__icontains=q))
               .select_related("device").order_by("ip_address")[:200])
        mac = (MACEntry.objects
               .filter(Q(mac_address__icontains=mac_norm) | Q(mac_address__icontains=q))
               .select_related("device").order_by("mac_address")[:200])
        vendors = _vendor_map([e.mac_address for e in arp] + [e.mac_address for e in mac])

        def dev(e):
            return {"device_id": e.device_id, "device_hostname": e.device.hostname}

        return Response({
            "query": q,
            "arp": [{**_arp_dict(e, vendors), **dev(e)} for e in arp],
            "mac": [{**_mac_dict(e, vendors), **dev(e)} for e in mac],
        })


# Bound the in-Python aggregation so a pathologically large fleet can't OOM the
# worker. Current-state ARP/MAC tables are modest (a few thousand rows), so
# aggregating then sorting/slicing in Python is fine for v1 and lets every column
# sort cleanly. If a fleet's tables grow past this, move to keyset SQL (group by
# mac with a window for the preferred location) rather than raising the cap.
HOSTS_SCAN_CAP = 100_000

# Aggregated uplink/trunk ports by name: AOS-CX lagN, Junos aeN, Cisco
# Port-channelN/PoN, ArubaOS-S TrkN (optionally with a .unit suffix).
_UPLINK_PORT_RE = re.compile(r"^(?:lag|ae|port-channel|po|trk)\d+(?:\.\d+)?$", re.IGNORECASE)


def is_uplink_port(port: str) -> bool:
    return bool(_UPLINK_PORT_RE.match((port or "").strip()))


_HOST_SORTS = {"mac", "vendor", "ip", "device", "port", "vlan", "type", "last_seen"}


def _ip_int(ip: str) -> int:
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return -1


def _host_sort_key(row: dict, sort: str):
    """A consistently-typed sort key per column (empties coerce to a floor value
    so asc/desc reverse cleanly without None comparisons)."""
    if sort == "vendor":
        return (row["vendor"] or "").lower()
    if sort == "ip":
        return min((_ip_int(i) for i in row["ips"]), default=-1)
    if sort == "device":
        return (row["device"] or "").lower()
    if sort == "port":
        return (row["port"] or "").lower()
    if sort == "vlan":
        return row["vlan"] if row["vlan"] is not None else -1
    if sort == "type":
        return row["entry_type"] or ""
    if sort == "last_seen":
        return row["_last_seen_ts"]
    return row["mac"]  # default/mac


class NetworkHostsView(APIView):
    """Unified, browsable IP/MAC table — one row per MAC across the whole fleet.

    Joins ARP and MAC-table sightings by MAC so a host's L3 presence (router ARP)
    and L2 location (switchport) collapse into a single row even when they were
    seen on different devices. Includes MACs that appear only in ARP (router-
    learned, no switchport). Default-all, filtered, sorted and paginated.
    """
    permission_classes = [HasCapability("device:view")]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip().lower()
        sort = request.query_params.get("sort") or "mac"
        if sort not in _HOST_SORTS:
            sort = "mac"
        order = request.query_params.get("order") or "asc"
        desc = order == "desc"
        page = max(1, _int(request.query_params.get("page"), 1))
        page_size = min(500, max(1, _int(request.query_params.get("page_size"), 100)))

        arp_rows = list(ARPEntry.objects.select_related("device")
                        .order_by("mac_address")[:HOSTS_SCAN_CAP])
        mac_rows = list(MACEntry.objects.select_related("device")
                        .order_by("mac_address")[:HOSTS_SCAN_CAP])

        vendors = _vendor_map([e.mac_address for e in arp_rows]
                              + [e.mac_address for e in mac_rows])

        # Aggregate per MAC. Location prefers a MAC-table sighting (switchport);
        # falls back to the ARP device when a MAC only ever appears in ARP.
        hosts: dict[str, dict] = {}
        last_overall = None

        def _bump(ts):
            nonlocal last_overall
            if ts and (last_overall is None or ts > last_overall):
                last_overall = ts

        for e in arp_rows:
            h = hosts.setdefault(e.mac_address, _blank_host(e.mac_address, vendors))
            if e.ip_address not in h["ips"]:
                h["ips"].append(e.ip_address)
            # ARP is the fallback location only — don't overwrite a MAC sighting.
            if not h["_has_mac_loc"] and e.collected_at and e.collected_at > h["_arp_loc_ts"]:
                h["_arp_loc_ts"] = e.collected_at
                h["device_id"], h["device"] = e.device_id, e.device.hostname
                h["port"] = e.interface
                if e.vlan is not None:
                    h["vlan"] = e.vlan
                h["entry_type"] = h["entry_type"] or e.entry_type
            _touch_last_seen(h, e.collected_at)
            _bump(e.collected_at)

        for e in mac_rows:
            h = hosts.setdefault(e.mac_address, _blank_host(e.mac_address, vendors))
            # A MAC-table sighting is the authoritative location. Every switch
            # also sees the MAC on its uplink, so an access-port sighting beats
            # an uplink one; within the same kind, most recent wins.
            uplink = is_uplink_port(e.interface)
            rank = 1 if uplink else 2
            if rank > h["_mac_rank"] or (
                    rank == h["_mac_rank"] and e.collected_at and e.collected_at > h["_mac_loc_ts"]):
                h["_has_mac_loc"] = True
                h["_mac_rank"] = rank
                h["_uplink"] = uplink
                h["_mac_loc_ts"] = e.collected_at or h["_mac_loc_ts"]
                h["device_id"], h["device"] = e.device_id, e.device.hostname
                h["port"] = e.interface
                h["vlan"] = e.vlan
                h["entry_type"] = e.entry_type
            _touch_last_seen(h, e.collected_at)
            _bump(e.collected_at)

        rows = list(hosts.values())
        for h in rows:
            h["ips"].sort(key=_ip_int)

        if q:
            rows = [h for h in rows if _host_matches(h, q)]
        # Hide hosts only ever seen on an uplink (no access-port location).
        if request.query_params.get("exclude_uplinks") in ("1", "true"):
            rows = [h for h in rows if not h["_uplink"]]

        rows.sort(key=lambda h: _host_sort_key(h, sort), reverse=desc)

        # Every matching row (no pagination), in the table's current sort.
        # Not "?format=" — DRF reserves that for renderer selection.
        if request.query_params.get("export") == "csv":
            return _hosts_csv(rows)

        count = len(rows)
        num_pages = max(1, ceil(count / page_size))
        start = (page - 1) * page_size
        window = rows[start:start + page_size]

        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "num_pages": num_pages,
            "last_collected": last_overall.isoformat() if last_overall else None,
            "results": [_host_public(h) for h in window],
        })


def _hosts_csv(rows: list[dict]):
    import csv
    from datetime import date

    from django.http import HttpResponse

    from apps.core.audit import csv_safe

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = (
        f'attachment; filename="ip-mac-hosts-{date.today():%Y%m%d}.csv"')
    w = csv.writer(resp)
    w.writerow(["MAC", "Vendor", "IP Addresses", "Device", "Port", "VLAN",
                "Type", "Last Seen"])
    # csv_safe: ports, hostnames and vendor strings come from devices.
    for h in rows:
        row = [csv_safe(c) for c in (
            h["mac"], h["vendor"], " ".join(h["ips"]), h["device"], h["port"],
            "" if h["vlan"] is None else h["vlan"], h["entry_type"], h["last_seen"] or "",
        )]
        row[4] = _excel_text(h["port"] or "") or row[4]
        w.writerow(row)
    return resp


# Ports like "1/1/16" are read by Excel as dates (quoting doesn't stop it).
_EXCEL_COERCED_RE = re.compile(r"^[\d/\-.: ]+$")


def _excel_text(value: str) -> str:
    """``="1/1/16"`` for an all-digit/separator value Excel would coerce to a
    date or number — it evaluates to the literal text. '' when no wrap is
    needed. Safe as a formula: the pattern admits no quotes or letters."""
    return f'="{value}"' if _EXCEL_COERCED_RE.match(value) else ""


def _int(val, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _blank_host(mac: str, vendors: dict) -> dict:
    from datetime import datetime, timezone
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return {
        "mac": mac, "vendor": vendors.get(mac, ""), "ips": [],
        "device_id": None, "device": "", "port": "", "vlan": None,
        "entry_type": "", "last_seen": None,
        "_last_seen_ts": floor, "_arp_loc_ts": floor, "_mac_loc_ts": floor,
        "_has_mac_loc": False, "_mac_rank": 0, "_uplink": False,
    }


def _touch_last_seen(h: dict, ts) -> None:
    if ts and ts > h["_last_seen_ts"]:
        h["_last_seen_ts"] = ts
        h["last_seen"] = ts.isoformat()


def _host_matches(h: dict, q: str) -> bool:
    if q in h["mac"].lower() or q in (h["vendor"] or "").lower():
        return True
    if q in (h["device"] or "").lower() or q in (h["port"] or "").lower():
        return True
    return any(q in ip.lower() for ip in h["ips"])


def _host_public(h: dict) -> dict:
    return {
        "mac": h["mac"], "vendor": h["vendor"], "ips": h["ips"],
        "device_id": h["device_id"], "device": h["device"],
        "port": h["port"], "vlan": h["vlan"],
        "entry_type": h["entry_type"], "last_seen": h["last_seen"],
        "uplink": h["_uplink"],
    }


class MACVendorView(APIView):
    permission_classes = [HasCapability("device:view")]

    def get(self, request, mac):
        oui = oui_of(mac)
        vendor = ""
        if oui:
            row = MACVendor.objects.filter(oui=oui).first()
            vendor = row.vendor if row else ""
        return Response({"mac": normalize_mac(mac), "oui": oui, "vendor": vendor})
