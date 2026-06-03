"""REST endpoints for ARP/MAC tables, global IP/MAC search and OUI lookup."""
from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.devices.models import Device
from .models import ARPEntry, MACEntry, MACVendor
from .normalize import normalize_mac, oui_of


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
        "vlan": e.vlan, "protocol": e.protocol, "age_minutes": e.age_minutes,
        "collected_at": e.collected_at.isoformat(),
    }


def _mac_dict(e: MACEntry, vendors: dict) -> dict:
    return {
        "id": e.id, "mac_address": e.mac_address, "vendor": vendors.get(e.mac_address, ""),
        "vlan": e.vlan, "interface": e.interface, "entry_type": e.entry_type,
        "collected_at": e.collected_at.isoformat(),
    }


class DeviceARPView(APIView):
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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


class DeviceARPMACCollectView(APIView):
    """Trigger an immediate ARP/MAC collection for one device (runs inline)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        from apps.compliance.collector import get_credentials
        from .collector import DEVICE_TYPE_MAP, collect_arp_mac, store_arp_mac

        if (device.platform or "").lower() not in DEVICE_TYPE_MAP:
            return Response({"error": f"ARP/MAC collection not supported for platform '{device.platform}'."},
                            status=status.HTTP_400_BAD_REQUEST)
        profile = device.credential_profile
        username = profile.ssh_username if profile else ""
        secrets = get_credentials(device)
        if not username or not secrets.get("ssh_password"):
            return Response({"error": "No SSH credentials configured for this device."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            arp, mac = collect_arp_mac(device, secrets, username)
            n_arp, n_mac = store_arp_mac(device, arp, mac)
        except Exception as exc:
            return Response({"error": f"Collection failed: {exc}"},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({"arp": n_arp, "mac": n_mac})


class NetworkSearchView(APIView):
    """Find which device(s) see a given IP or MAC — 'where is this host?'."""
    permission_classes = [IsAuthenticated]

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


class MACVendorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, mac):
        oui = oui_of(mac)
        vendor = ""
        if oui:
            row = MACVendor.objects.filter(oui=oui).first()
            vendor = row.vendor if row else ""
        return Response({"mac": normalize_mac(mac), "oui": oui, "vendor": vendor})
