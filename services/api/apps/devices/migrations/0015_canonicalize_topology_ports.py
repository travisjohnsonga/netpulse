"""
Canonicalise existing TopologyLink interface names (Gi3 → GigabitEthernet3) and
remove the duplicate rows that abbreviated/full spellings produced. Keeps the
most recently seen row of each canonical link.
"""
from django.db import migrations

# Inlined (migrations must not import app code that may change later).
_IFNAME_PREFIXES = {
    "gigabitethernet": "GigabitEthernet", "gige": "GigabitEthernet", "gi": "GigabitEthernet",
    "tengigabitethernet": "TenGigabitEthernet", "tengige": "TenGigabitEthernet", "te": "TenGigabitEthernet",
    "twentyfivegige": "TwentyFiveGigE",
    "fortygigabitethernet": "FortyGigabitEthernet", "fortygige": "FortyGigabitEthernet", "fo": "FortyGigabitEthernet",
    "hundredgige": "HundredGigE", "hu": "HundredGigE",
    "fastethernet": "FastEthernet", "fa": "FastEthernet",
    "ethernet": "Ethernet", "eth": "Ethernet", "et": "Ethernet",
    "port-channel": "Port-channel", "portchannel": "Port-channel", "po": "Port-channel",
    "loopback": "Loopback", "lo": "Loopback",
    "vlan": "Vlan", "vl": "Vlan",
    "tunnel": "Tunnel", "tu": "Tunnel",
    "management": "Management", "mgmt": "Management",
}


def _canon(name):
    import re
    raw = (name or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z-]*?)\s*([\d/.:]+.*)$", raw)
    if not m:
        return raw
    full = _IFNAME_PREFIXES.get(m.group(1).lower())
    return f"{full}{m.group(2)}" if full else raw


def forwards(apps, schema_editor):
    TopologyLink = apps.get_model("devices", "TopologyLink")
    rows = list(TopologyLink.objects.all().order_by("-last_seen", "-id"))

    keep = {}  # canonical key → kept row id
    to_delete = []
    for r in rows:
        key = (r.device_a_id, _canon(r.port_a), r.device_b_id, _canon(r.port_b))
        if key in keep:
            to_delete.append(r.id)
        else:
            keep[key] = r.id
    # Delete duplicates first so canonicalising the survivors can't collide.
    TopologyLink.objects.filter(id__in=to_delete).delete()
    for r in TopologyLink.objects.filter(id__in=list(keep.values())):
        pa, pb = _canon(r.port_a), _canon(r.port_b)
        if (pa, pb) != (r.port_a, r.port_b):
            r.port_a, r.port_b = pa, pb
            r.save(update_fields=["port_a", "port_b"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("devices", "0014_discoveryjob_cancel_requested")]
    operations = [migrations.RunPython(forwards, noop)]
