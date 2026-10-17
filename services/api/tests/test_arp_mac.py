"""ARP/MAC collection: normalization, parsing, persistence, and API."""
import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    from apps.devices.models import Device
    return Device.objects.create(hostname="sw1", ip_address="192.0.2.21",
                                 management_ip="192.0.2.21", platform="aos_cx", status="active")


# ── normalization ─────────────────────────────────────────────────────────────
class TestNormalizeMac:
    def test_dotted_cisco_form(self):
        from apps.arp_mac.normalize import normalize_mac
        assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"

    def test_hyphen_and_upper(self):
        from apps.arp_mac.normalize import normalize_mac
        assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"

    def test_already_colon(self):
        from apps.arp_mac.normalize import normalize_mac
        assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"

    def test_invalid_passthrough(self):
        from apps.arp_mac.normalize import normalize_mac
        assert normalize_mac("incomplete") == "incomplete"
        assert normalize_mac("") == "" and normalize_mac(None) == ""

    def test_oui(self):
        from apps.arp_mac.normalize import oui_of
        assert oui_of("AABB.CCDD.EEFF") == "aa:bb:cc"
        assert oui_of("bad") == ""


# ── parsing / field normalization ─────────────────────────────────────────────
class TestParsing:
    def test_normalize_arp_varied_keys(self):
        from apps.arp_mac.collector import _normalize_arp
        rows = [
            {"address": "10.0.0.1", "mac": "aabb.ccdd.eeff", "age": "5", "interface": "vlan1", "protocol": "Internet"},
            {"ip_address": "10.0.0.2", "mac_address": "11:22:33:44:55:66", "interface": "1/1/2"},
            {"ip_address": "10.0.0.3", "mac_address": "22:33:44:55:66:77", "flags": "Static"},
        ]
        out = _normalize_arp(rows)
        assert out[0] == {"ip_address": "10.0.0.1", "mac_address": "aa:bb:cc:dd:ee:ff",
                          "interface": "vlan1", "vlan": None, "age_minutes": 5,
                          "protocol": "Internet", "entry_type": "dynamic"}
        assert out[1]["mac_address"] == "11:22:33:44:55:66" and out[1]["protocol"] == "Internet"
        assert out[1]["entry_type"] == "dynamic"     # no flag → dynamic
        assert out[2]["entry_type"] == "static"      # static flag honored

    def test_normalize_mac_varied_keys(self):
        from apps.arp_mac.collector import _normalize_mac
        rows = [
            {"destination_address": "aabb.ccdd.eeff", "vlan": "10", "type": "DYNAMIC", "destination_port": "1/1/5"},
            {"mac": "11:22:33:44:55:66", "vlan": "1", "ports": "Gi0/1"},
        ]
        out = _normalize_mac(rows)
        assert out[0] == {"mac_address": "aa:bb:cc:dd:ee:ff", "vlan": 10,
                          "interface": "1/1/5", "entry_type": "dynamic"}
        assert out[1]["interface"] == "Gi0/1" and out[1]["entry_type"] == "dynamic"

    def test_sonicwall_arp_textfsm(self):
        from apps.arp_mac.collector import _parse_sonicwall_arp
        # Note: 2+ spaces separate the (space-containing) vendor from interface.
        sample = (
            "===================\n"
            "Current ARP caches:\n"
            "===================\n"
            "IP Address     Type     MAC Address        Vendor      Interface  Timeout\n"
            "198.51.100.129  Static   1A:C2:41:2C:0B:0C  SONICWALL                   X0:V1000   Permanent published\n"
            "198.51.100.135  Dynamic  9C:37:08:25:F3:40  HEWLETT PACKARD ENTERPRISE  X0:V1000   Expires in 10 minutes  10\n"
            "198.51.100.11   Dynamic  D4:A2:CD:13:5C:FB  DELL                        X0:V201    Expires in 2 minutes   2\n"
        )
        out = _parse_sonicwall_arp(sample)
        assert out[0] == {"ip_address": "198.51.100.129", "mac_address": "1a:c2:41:2c:0b:0c",
                          "interface": "X0:V1000", "vlan": None, "age_minutes": None,
                          "protocol": "Internet", "entry_type": "static"}   # Static row
        assert out[1]["mac_address"] == "9c:37:08:25:f3:40" and out[1]["age_minutes"] == 10
        assert out[1]["entry_type"] == "dynamic"                            # Dynamic row
        assert out[2]["interface"] == "X0:V201" and out[2]["age_minutes"] == 2

    def test_sonicwall_shell_double_password_and_pager_disable(self):
        from apps.arp_mac.collector import _drive_sonicwall_shell, _parse_sonicwall_arp

        class _FakeShell:
            """Simulates SonicOS: banner re-prompts for the password, then
            'no cli pager session' disables paging so the full ARP table returns
            in one unpaged reply."""
            def __init__(self):
                # Banner ends asking for the password AGAIN (double password).
                self._queue = [b"Copyright (c) 2024 SonicWall, Inc.\r\nAccess denied\r\nPassword:"]
                self.sent = []
                self._authed = False

            def recv_ready(self):
                return bool(self._queue)

            def recv(self, _n):
                return self._queue.pop(0)

            def send(self, data):
                self.sent.append(data)
                if data == "s3cret\n" and not self._authed:
                    self._authed = True
                    self._queue.append(b"\r\nhostname> ")
                elif data.strip() == "no cli pager session":
                    self._queue.append(b"\r\nhostname> ")  # pager-disable ack to drain
                elif data.strip() == "show arp caches":
                    # Paging disabled → full table in one reply, no --More--.
                    self._queue.append(
                        b"IP Address     Type     MAC Address        Vendor      Interface  Timeout\r\n"
                        b"198.51.100.129  Static   1A:C2:41:2C:0B:0C  SONICWALL                   X0:V1000   Permanent published\r\n"
                        b"198.51.100.135  Dynamic  9C:37:08:25:F3:40  HEWLETT PACKARD ENTERPRISE  X0:V1000   Expires in 10 minutes  10\r\n"
                        b"hostname> ")

        shell = _FakeShell()
        out = _drive_sonicwall_shell(shell, "show arp caches", "s3cret",
                                     banner_wait=0, drain_wait=0, cmd_wait=0,
                                     settle=0, max_idle=2)
        assert "s3cret\n" in shell.sent                  # re-sent the password on the shell prompt
        assert "no cli pager session\n" in shell.sent    # disabled paging as its own command
        assert " " not in shell.sent                     # no --More-- space-advancing needed
        assert "--More--" not in out
        entries = _parse_sonicwall_arp(out)
        assert {e["ip_address"] for e in entries} == {"198.51.100.129", "198.51.100.135"}
        assert entries[0]["entry_type"] == "static"

    def test_fortios_arp_regex(self):
        from apps.arp_mac.collector import _parse_fortios_arp
        out = _parse_fortios_arp(
            "Address           Age(min)   Hardware Addr      Interface\n"
            "192.0.2.1        0          aa:bb:cc:dd:ee:ff  port1\n")
        assert out == [{"ip_address": "192.0.2.1", "mac_address": "aa:bb:cc:dd:ee:ff",
                        "interface": "port1", "vlan": None, "age_minutes": 0,
                        "protocol": "Internet", "entry_type": "dynamic"}]


class TestAOSCXRest:
    """AOS-CX collection prefers the REST API and falls back to SSH on failure."""

    class _FakeRestClient:
        def __init__(self, arp, mac):
            self._arp, self._mac = arp, mac

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            return {}

        def get_arp_table(self):
            return [dict(r) for r in self._arp]

        def get_mac_table(self):
            return [dict(r) for r in self._mac]

    def test_rest_preferred_no_ssh(self, device, monkeypatch):
        from apps.arp_mac import collector
        arp = [{"ip_address": "192.0.2.1", "mac_address": "1A:C2:41:2C:0B:0C",
                "interface": "vlan1", "vlan": 1, "age_minutes": None,
                "protocol": "Internet", "entry_type": "dynamic"}]
        mac = [{"mac_address": "00:09:01:12:A6:C3", "vlan": 1,
                "interface": "lag2", "entry_type": "dynamic"}]
        monkeypatch.setattr("apps.devices.aos_cx_client.AOSCXClient",
                            lambda host, **kw: self._FakeRestClient(arp, mac))

        arp_out, mac_out = collector.collect_arp_mac(
            device, {"ssh_password": "pw"}, "travis-admin")

        assert arp_out[0]["ip_address"] == "192.0.2.1"
        assert arp_out[0]["mac_address"] == "1a:c2:41:2c:0b:0c"   # normalized
        assert mac_out[0]["mac_address"] == "00:09:01:12:a6:c3"   # normalized

    def test_rest_failure_falls_back_to_ssh(self, device, monkeypatch):
        from apps.arp_mac import collector

        def _boom(host, username, password):
            return None   # REST unusable → SSH fallback
        monkeypatch.setattr(collector, "_collect_aos_cx_rest", _boom)

        called = {}

        class _Conn:
            def send_command(self, *a, **k):
                return []

            def disconnect(self):
                called["disconnected"] = True

        import netmiko
        monkeypatch.setattr(netmiko, "ConnectHandler", lambda **kw: _Conn())

        arp_out, mac_out = collector.collect_arp_mac(
            device, {"ssh_password": "pw"}, "travis-admin")
        assert arp_out == [] and mac_out == []
        assert called.get("disconnected") is True


def _jleaf(v):
    return [{"data": v}]


def _junos_vlans_json():
    import json
    groups = [{"l2ng-l2rtb-name": _jleaf("default-switch"),
               "l2ng-l2rtb-vlan-name": _jleaf(name),
               "l2ng-l2rtb-vlan-tag": _jleaf(str(tag))}
              for name, tag in [("USERS", 201), ("NATIVE", 999), ("MGMT", 1000), ("default", 1)]]
    return json.dumps({"l2ng-l2ald-vlan-instance-information": [
        {"l2ng-l2ald-vlan-instance-group": groups}]})


def _junos_mac_json():
    """Shape of a real EX4100 (Junos 23.4, ELS) brief-rtb table: one group whose
    group-level vlan-id is NOT the per-entry VLAN — entries carry the name."""
    import json
    rows = [("USERS", "00:00:5e:00:53:01", "D", "ge-0/0/1.0"),
            ("NATIVE", "00:00:5e:00:53:01", "D", "ae9.0"),      # same MAC, 2nd VLAN
            ("MGMT", "00:00:5e:00:53:02", "S", "ae9.0"),
            ("default", "00:00:5e:00:53:03", "D", "ge-0/0/2.0")]
    entries = [{"l2ng-l2-mac-vlan-name": _jleaf(v), "l2ng-l2-mac-address": _jleaf(m),
                "l2ng-l2-mac-flags": _jleaf(f), "l2ng-l2-mac-age": _jleaf("-"),
                "l2ng-l2-mac-logical-interface": _jleaf(i),
                "l2ng-l2-mac-fwd-next-hop": _jleaf("0"), "l2ng-l2-mac-rtr-id": _jleaf("0")}
               for v, m, f, i in rows]
    return json.dumps({"l2ng-l2ald-rtb-macdb": [{"l2ng-l2ald-mac-entry-vlan": [{
        "attributes": {"junos:style": "brief-rtb"},
        "l2ng-l2-vlan-id": _jleaf("201"),
        "l2ng-mac-entry": entries}]}]})


class TestJunosMac:
    """Junos MAC table is parsed from JSON (the ntc text template can't parse ELS)."""

    def test_vlan_name_to_tag(self):
        from apps.arp_mac.collector import _parse_junos_vlans
        assert _parse_junos_vlans(_junos_vlans_json()) == {
            "USERS": 201, "NATIVE": 999, "MGMT": 1000, "default": 1}

    def test_parse_mac_entries(self):
        from apps.arp_mac.collector import _parse_junos_mac, _parse_junos_vlans
        out = _parse_junos_mac(_junos_mac_json(), _parse_junos_vlans(_junos_vlans_json()))
        assert out == [
            {"mac_address": "00:00:5e:00:53:01", "vlan": 201, "interface": "ge-0/0/1", "entry_type": "dynamic"},
            {"mac_address": "00:00:5e:00:53:01", "vlan": 999, "interface": "ae9", "entry_type": "dynamic"},
            {"mac_address": "00:00:5e:00:53:02", "vlan": 1000, "interface": "ae9", "entry_type": "static"},
            {"mac_address": "00:00:5e:00:53:03", "vlan": 1, "interface": "ge-0/0/2", "entry_type": "dynamic"},
        ]

    def test_unknown_vlan_name_is_none(self):
        from apps.arp_mac.collector import _parse_junos_mac
        out = _parse_junos_mac(_junos_mac_json(), {})
        assert {e["vlan"] for e in out} == {None}

    @pytest.mark.parametrize("bad", ["", "error: syntax error", "{not json", None, []])
    def test_garbage_output_is_empty(self, bad):
        from apps.arp_mac.collector import _parse_junos_mac, _parse_junos_vlans
        assert _parse_junos_vlans(bad) == {}
        assert _parse_junos_mac(bad, {}) == []

    def test_collect_and_store_end_to_end(self, monkeypatch):
        from apps.arp_mac import collector
        from apps.arp_mac.models import MACEntry
        from apps.devices.models import Device
        dev = Device.objects.create(hostname="ex1", ip_address="192.0.2.30",
                                    platform="junos", status="active")
        sent = []

        class _Conn:
            def send_command(self, cmd, **kw):
                sent.append((cmd, kw.get("use_textfsm", False)))
                if cmd == "show vlans | display json":
                    return _junos_vlans_json()
                if cmd == "show ethernet-switching table | display json":
                    return _junos_mac_json()
                return []   # ARP via ntc-templates

            def disconnect(self):
                pass

        import netmiko
        monkeypatch.setattr(netmiko, "ConnectHandler", lambda **kw: _Conn())
        arp, mac = collector.collect_arp_mac(dev, {"ssh_password": "pw"}, "u")
        assert ("show ethernet-switching table | display json", False) in sent
        assert collector.store_arp_mac(dev, arp, mac) == (0, 4)
        # Same MAC in two VLANs is kept as two rows (was collapsed with vlan=None).
        assert MACEntry.objects.filter(device=dev, mac_address="00:00:5e:00:53:01").count() == 2

    def test_send_failure_does_not_raise(self, monkeypatch):
        from apps.arp_mac import collector
        from apps.devices.models import Device
        dev = Device.objects.create(hostname="ex2", ip_address="192.0.2.31",
                                    platform="junos", status="active")

        class _Conn:
            def send_command(self, cmd, **kw):
                if "display json" in cmd:
                    raise OSError("socket closed")
                return []

            def disconnect(self):
                pass

        import netmiko
        monkeypatch.setattr(netmiko, "ConnectHandler", lambda **kw: _Conn())
        assert collector.collect_arp_mac(dev, {"ssh_password": "pw"}, "u") == ([], [])


# ── persistence ───────────────────────────────────────────────────────────────
class TestStore:
    def test_arp_upsert_and_mac_replace(self, device):
        from apps.arp_mac.collector import store_arp_mac
        from apps.arp_mac.models import ARPEntry, MACEntry
        arp = [{"ip_address": "10.0.0.1", "mac_address": "aa:bb:cc:dd:ee:ff",
                "interface": "vlan1", "entry_type": "static"}]
        mac = [{"mac_address": "aa:bb:cc:dd:ee:ff", "vlan": 1, "interface": "1/1/5", "entry_type": "dynamic"}]
        n_arp, n_mac = store_arp_mac(device, arp, mac)
        assert (n_arp, n_mac) == (1, 1)
        assert ARPEntry.objects.filter(device=device).count() == 1
        assert ARPEntry.objects.get(device=device, ip_address="10.0.0.1").entry_type == "static"

        # Second collection: no entry_type → defaults to dynamic.
        store_arp_mac(device, [{"ip_address": "10.0.0.2", "mac_address": "aa:bb:cc:dd:ee:ff"}], [])
        assert ARPEntry.objects.get(device=device, ip_address="10.0.0.2").entry_type == "dynamic"

        # Third collection: ip changes MAC (upsert) + stale ips are dropped.
        store_arp_mac(device, [{"ip_address": "10.0.0.1", "mac_address": "11:22:33:44:55:66"}], [])
        assert ARPEntry.objects.get(device=device, ip_address="10.0.0.1").mac_address == "11:22:33:44:55:66"
        assert MACEntry.objects.filter(device=device).count() == 0  # replaced with empty


# ── API ───────────────────────────────────────────────────────────────────────
class TestApi:
    def _seed(self, device):
        from apps.arp_mac.models import ARPEntry, MACEntry, MACVendor
        MACVendor.objects.create(oui="aa:bb:cc", vendor="Acme Networks")
        ARPEntry.objects.create(device=device, ip_address="10.0.0.1",
                                mac_address="aa:bb:cc:dd:ee:ff", interface="vlan1", entry_type="static")
        MACEntry.objects.create(device=device, mac_address="aa:bb:cc:dd:ee:ff", vlan=1, interface="1/1/5")

    def test_arp_requires_auth(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/arp/").status_code == 401

    def test_arp_returns_entries_with_vendor(self, auth_client, device):
        self._seed(device)
        r = auth_client.get(f"/api/devices/{device.id}/arp/")
        assert r.status_code == 200
        assert r.data["count"] == 1
        assert r.data["results"][0]["vendor"] == "Acme Networks"
        assert r.data["results"][0]["entry_type"] == "static"
        assert r.data["last_collected"] is not None

    def test_mac_filter_by_vlan(self, auth_client, device):
        self._seed(device)
        assert auth_client.get(f"/api/devices/{device.id}/mac/?vlan=1").data["count"] == 1
        assert auth_client.get(f"/api/devices/{device.id}/mac/?vlan=99").data["count"] == 0

    def test_network_search_by_ip_and_mac(self, auth_client, device):
        self._seed(device)
        r = auth_client.get("/api/network/search/?q=10.0.0.1")
        assert r.status_code == 200 and len(r.data["arp"]) == 1
        assert r.data["arp"][0]["device_hostname"] == "sw1"
        r2 = auth_client.get("/api/network/search/?q=aabb.ccdd.eeff")
        assert len(r2.data["mac"]) == 1  # dotted form normalized to match

    def test_mac_vendor_lookup(self, auth_client, device):
        self._seed(device)
        r = auth_client.get("/api/network/mac-vendor/aa:bb:cc:dd:ee:ff/")
        assert r.status_code == 200 and r.data["vendor"] == "Acme Networks"
        assert r.data["oui"] == "aa:bb:cc"


# ── unified hosts endpoint ──────────────────────────────────────────────────────
@pytest.fixture
def switch(db):
    from apps.devices.models import Device
    return Device.objects.create(hostname="sw2", ip_address="10.150.0.25",
                                 management_ip="10.150.0.25", platform="aos_cx", status="active")


def _seed_hosts(router, switch):
    """Three fleet hosts:
      H1 (cisco1) — MAC-table sighting on the switch + ARP on the router (joined by MAC)
      H2 (cisco2) — MAC + ARP, same join
      H3 (acme)   — router-learned ARP only, no switchport
    """
    from apps.arp_mac.models import ARPEntry, MACEntry, MACVendor
    MACVendor.objects.create(oui="00:00:0c", vendor="Cisco Systems, Inc")
    MACVendor.objects.create(oui="aa:bb:cc", vendor="Acme Networks")
    # H1 — switchport location is 1/1/3 on the switch; ARP seen on the router.
    MACEntry.objects.create(device=switch, mac_address="00:00:0c:00:00:01",
                            vlan=30, interface="1/1/3", entry_type="static")
    ARPEntry.objects.create(device=router, ip_address="10.0.0.30",
                            mac_address="00:00:0c:00:00:01", interface="vlan30", entry_type="dynamic")
    # H2
    MACEntry.objects.create(device=switch, mac_address="00:00:0c:00:00:02",
                            vlan=10, interface="1/1/1", entry_type="dynamic")
    ARPEntry.objects.create(device=router, ip_address="10.0.0.10",
                            mac_address="00:00:0c:00:00:02", interface="vlan10", entry_type="dynamic")
    # H3 — ARP only (no MAC-table sighting anywhere).
    ARPEntry.objects.create(device=router, ip_address="10.0.0.200",
                            mac_address="aa:bb:cc:dd:ee:ff", interface="vlan99", entry_type="dynamic")


def _ip_int(ip):
    import ipaddress
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return -1


def _expected_key(row, sort):
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
        return row["last_seen"] or ""
    return row["mac"]


class TestHostsEndpoint:
    def test_requires_auth(self, api_client, device):
        assert api_client.get("/api/network/hosts/").status_code == 401

    def test_returns_all_by_default(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/")
        assert r.status_code == 200
        assert r.data["count"] == 3
        assert len(r.data["results"]) == 3
        assert r.data["last_collected"] is not None

    def test_arp_ip_joined_by_mac(self, auth_client, device, switch):
        """H1's ARP IP (router) and switchport (switch) collapse into one row,
        with the MAC-table sighting as the authoritative location."""
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/")
        h1 = next(h for h in r.data["results"] if h["mac"] == "00:00:0c:00:00:01")
        assert h1["ips"] == ["10.0.0.30"]
        assert h1["device"] == "sw2"            # the switch (MAC sighting), not the router
        assert h1["port"] == "1/1/3"
        assert h1["vlan"] == 30
        assert h1["entry_type"] == "static"
        assert h1["vendor"] == "Cisco Systems, Inc"

    def test_arp_only_mac_included(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/")
        acme = next(h for h in r.data["results"] if h["mac"] == "aa:bb:cc:dd:ee:ff")
        assert acme["ips"] == ["10.0.0.200"]
        assert acme["device"] == "sw1"          # falls back to the ARP device (the router)
        assert acme["port"] == "vlan99"
        assert acme["vendor"] == "Acme Networks"

    def test_pagination(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?page_size=2")
        assert r.data["count"] == 3
        assert r.data["page_size"] == 2
        assert r.data["num_pages"] == 2
        assert r.data["page"] == 1
        assert len(r.data["results"]) == 2
        r2 = auth_client.get("/api/network/hosts/?page_size=2&page=2")
        assert r2.data["page"] == 2
        assert len(r2.data["results"]) == 1

    def test_page_size_capped(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?page_size=99999")
        assert r.data["page_size"] == 500

    @pytest.mark.parametrize("sort", ["mac", "vendor", "ip", "device", "port", "vlan", "type", "last_seen"])
    def test_sort_keys_and_order(self, auth_client, device, switch, sort):
        _seed_hosts(device, switch)
        asc = auth_client.get(f"/api/network/hosts/?sort={sort}&order=asc").data["results"]
        desc = auth_client.get(f"/api/network/hosts/?sort={sort}&order=desc").data["results"]
        asc_keys = [_expected_key(h, sort) for h in asc]
        desc_keys = [_expected_key(h, sort) for h in desc]
        # Tied keys keep insertion order (stable sort), so compare against the
        # sorted key sequence rather than an exact reverse of the asc result.
        assert asc_keys == sorted(asc_keys), f"{sort} asc not ordered"
        assert desc_keys == sorted(desc_keys, reverse=True), f"{sort} desc not ordered"

    def test_q_filter_ip(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?q=10.0.0.200")
        assert r.data["count"] == 1
        assert r.data["results"][0]["mac"] == "aa:bb:cc:dd:ee:ff"

    def test_q_filter_vendor_and_port(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        assert auth_client.get("/api/network/hosts/?q=cisco").data["count"] == 2
        r = auth_client.get("/api/network/hosts/?q=1/1/3")
        assert r.data["count"] == 1 and r.data["results"][0]["port"] == "1/1/3"

    def test_vendor_resolved_from_bundled_snapshot(self, auth_client, device):
        """Loading the bundled OUI snapshot (offline) resolves a real OUI."""
        from django.core.management import call_command
        from apps.arp_mac.models import ARPEntry
        call_command("update_mac_vendors")   # no args → bundled, offline
        ARPEntry.objects.create(device=device, ip_address="10.0.0.5",
                                mac_address="00:00:0c:ab:cd:ef", interface="vlan1")
        r = auth_client.get("/api/network/hosts/")
        host = next(h for h in r.data["results"] if h["mac"] == "00:00:0c:ab:cd:ef")
        assert "Cisco" in host["vendor"]


# ── offline-first OUI population ────────────────────────────────────────────────
@pytest.fixture
def core(db):
    from apps.devices.models import Device
    return Device.objects.create(hostname="core1", ip_address="192.0.2.50",
                                 platform="junos", status="active")


def _seed_uplinks(switch, core):
    """H1 is on access port 1/1/3 of sw2 and (seen later) on core1's uplink ae9;
    H4 is only ever seen on uplinks (lag10 on sw2, ae9 on core1)."""
    from apps.arp_mac.models import MACEntry
    MACEntry.objects.get_or_create(device=switch, mac_address="00:00:0c:00:00:01",
                                   vlan=30, defaults={"interface": "1/1/3"})   # also in _seed_hosts
    MACEntry.objects.create(device=core, mac_address="00:00:0c:00:00:01",
                            vlan=30, interface="ae9")             # newer, but an uplink
    MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:04",
                            vlan=30, interface="lag10")
    MACEntry.objects.create(device=core, mac_address="00:00:5e:00:53:04",
                            vlan=30, interface="ae9")


class TestUplinks:
    @pytest.mark.parametrize("port,expected", [
        ("lag10", True), ("ae9", True), ("ae9.0", True), ("Po1", True),
        ("Port-channel12", True), ("Trk1", True), ("vtep.32769", True), ("vtep", True),
        ("1/1/3", False), ("ge-0/0/1", False), ("Gi0/1", False), ("", False), (None, False),
        ("vlan30", False), ("aero1", False), ("vtep1", False), ("vteps", False),
    ])
    def test_is_uplink_port(self, port, expected):
        from apps.arp_mac.views import is_uplink_port
        assert is_uplink_port(port) is expected

    def test_access_port_beats_newer_uplink_sighting(self, auth_client, switch, core):
        _seed_uplinks(switch, core)
        rows = auth_client.get("/api/network/hosts/").data["results"]
        h1 = next(h for h in rows if h["mac"] == "00:00:0c:00:00:01")
        assert (h1["device"], h1["port"], h1["uplink"]) == ("sw2", "1/1/3", False)
        h4 = next(h for h in rows if h["mac"] == "00:00:5e:00:53:04")
        assert h4["uplink"] is True

    def test_exclude_uplinks_hides_uplink_only_hosts(self, auth_client, device, switch, core):
        _seed_hosts(device, switch)
        _seed_uplinks(switch, core)
        assert auth_client.get("/api/network/hosts/").data["count"] == 4
        r = auth_client.get("/api/network/hosts/?exclude_uplinks=true")
        macs = {h["mac"] for h in r.data["results"]}
        assert r.data["count"] == 3 and "00:00:5e:00:53:04" not in macs
        assert "aa:bb:cc:dd:ee:ff" in macs        # ARP-only host is not an uplink


class TestHostsCsv:
    def _csv(self, resp):
        import csv
        import io
        return list(csv.reader(io.StringIO(resp.content.decode())))

    def test_exports_all_rows_not_one_page(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?export=csv&page_size=1")
        assert r.status_code == 200 and r["Content-Type"].startswith("text/csv")
        assert 'filename="ip-mac-hosts-' in r["Content-Disposition"]
        rows = self._csv(r)
        assert rows[0] == ["MAC", "Vendor", "IP Addresses", "Device", "Port", "VLAN",
                           "Type", "Last Seen (UTC)"]
        assert len(rows) == 1 + 3
        h1 = next(x for x in rows if x[0] == "00:00:0c:00:00:01")
        assert h1[1:7] == ["Cisco Systems, Inc", "10.0.0.30", "sw2", "1/1/3", "30", "static"]

    def test_respects_filter_sort_and_uplinks(self, auth_client, device, switch, core):
        _seed_hosts(device, switch)
        _seed_uplinks(switch, core)
        rows = self._csv(auth_client.get(
            "/api/network/hosts/?export=csv&exclude_uplinks=true&sort=mac&order=desc"))
        macs = [x[0] for x in rows[1:]]
        assert macs == sorted(macs, reverse=True) and "00:00:5e:00:53:04" not in macs
        rows = self._csv(auth_client.get("/api/network/hosts/?export=csv&q=acme"))
        assert [x[0] for x in rows[1:]] == ["aa:bb:cc:dd:ee:ff"]

    def test_formula_injection_neutralized(self, auth_client, switch):
        from apps.arp_mac.models import MACEntry
        MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:09",
                                interface="=HYPERLINK(\"x\")")
        rows = self._csv(auth_client.get("/api/network/hosts/?export=csv"))
        assert rows[1][4].startswith("'=")

    def test_ports_are_plain_in_csv(self, auth_client, switch):
        # The CSV stays plain for scripts; Excel users get the .xlsx export.
        from apps.arp_mac.models import MACEntry
        MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:0a", interface="1/1/16")
        rows = self._csv(auth_client.get("/api/network/hosts/?export=csv"))
        assert rows[1][4] == "1/1/16"

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/network/hosts/?export=csv").status_code == 401


def _xlsx_cells(content: bytes) -> dict:
    """{"A1": (type, value)} from the first sheet. type: "s" shared string,
    "n" number, "f" formula. Read with zipfile — no spreadsheet lib needed."""
    import io
    import re
    import zipfile
    from xml.etree import ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(content))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
    cells = {}
    for c in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(f"{{{ns['m']}}}c"):
        v, f = c.find("m:v", ns), c.find("m:f", ns)
        if f is not None:
            cells[c.get("r")] = ("f", f.text)
        elif c.get("t") == "s":
            cells[c.get("r")] = ("s", shared[int(v.text)])
        elif v is not None:
            cells[c.get("r")] = ("n", v.text)
    assert all(re.match(r"^[A-Z]+\d+$", k) for k in cells)
    return cells


class TestHostsXlsx:
    def _get(self, client, query=""):
        r = client.get(f"/api/network/hosts/?export=xlsx{query}")
        assert r.status_code == 200
        return r

    def test_headers_and_filename(self, auth_client, device, switch):
        _seed_hosts(device, switch)
        r = self._get(auth_client, "&page_size=1")
        assert r["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert 'filename="ip-mac-hosts-' in r["Content-Disposition"]
        assert r["Content-Disposition"].endswith('.xlsx"')
        cells = _xlsx_cells(r.content)
        assert [cells[f"{c}1"][1] for c in "ABCDEFGH"] == [
            "MAC", "Vendor", "IP Addresses", "Device", "Port", "VLAN", "Type", "Last Seen (UTC)"]
        assert "A4" in cells and "A5" not in cells          # all 3 hosts, not one page

    @pytest.mark.parametrize("port", ["1/1/16", "1/1/51", "17", "1-2", "ge-0/0/1"])
    def test_port_is_a_text_cell(self, auth_client, switch, port):
        from apps.arp_mac.models import MACEntry
        MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:0a",
                                interface=port, vlan=30)
        cells = _xlsx_cells(self._get(auth_client).content)
        assert cells["E2"] == ("s", port)        # a string cell — never a date/number
        assert cells["F2"] == ("n", "30")        # VLAN stays numeric
        assert cells["H2"][0] == "n"             # Last Seen is a real date-time

    def test_formula_like_value_is_stored_as_text(self, auth_client, switch):
        from apps.arp_mac.models import MACEntry
        MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:09",
                                interface='=HYPERLINK("http://x")')
        cells = _xlsx_cells(self._get(auth_client).content)
        assert cells["E2"] == ("s", '=HYPERLINK("http://x")')
        assert not any(t == "f" for t, _ in cells.values())

    def test_respects_filter_and_uplinks(self, auth_client, device, switch, core):
        _seed_hosts(device, switch)
        _seed_uplinks(switch, core)
        cells = _xlsx_cells(self._get(auth_client, "&exclude_uplinks=true&q=00:00:0c").content)
        macs = [cells[k][1] for k in sorted(cells) if k[0] == "A" and k != "A1"]
        assert sorted(macs) == ["00:00:0c:00:00:01", "00:00:0c:00:00:02"]

    def test_empty_result_is_valid_workbook(self, auth_client, db):
        cells = _xlsx_cells(self._get(auth_client).content)
        assert cells["A1"] == ("s", "MAC") and "A2" not in cells

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/network/hosts/?export=xlsx").status_code == 401


class TestHostsReverseDns:
    """?resolve=1 adds reverse-DNS hostnames (apps.flows.dns.resolve_ips,
    mocked here) to the page, and a Hostname column to CSV/XLSX exports."""

    @pytest.fixture
    def fake_dns(self, monkeypatch):
        calls = {}

        def fake(ips, max_ips=100):
            calls["ips"], calls["max_ips"] = list(ips), max_ips
            names = {"10.0.0.30": "printer-1.example.com", "10.0.0.10": "laptop-2.example.com"}
            return {"resolved": {ip: names.get(ip, ip) for ip in ips}}
        monkeypatch.setattr("apps.flows.dns.resolve_ips", fake)
        return calls

    def test_page_hostnames(self, auth_client, device, switch, fake_dns):
        _seed_hosts(device, switch)
        rows = {h["mac"]: h for h in auth_client.get("/api/network/hosts/?resolve=1").data["results"]}
        assert rows["00:00:0c:00:00:01"]["hostname"] == "printer-1.example.com"
        assert rows["aa:bb:cc:dd:ee:ff"]["hostname"] == ""          # unresolved → blank, not the IP

    def test_no_resolve_no_lookup(self, auth_client, device, switch, fake_dns):
        _seed_hosts(device, switch)
        rows = auth_client.get("/api/network/hosts/").data["results"]
        assert "hostname" not in rows[0] and fake_dns == {}

    def test_only_page_window_resolved(self, auth_client, device, switch, fake_dns):
        _seed_hosts(device, switch)
        auth_client.get("/api/network/hosts/?resolve=1&page_size=1&sort=mac")
        assert len(fake_dns["ips"]) == 1

    def test_csv_hostname_column(self, auth_client, device, switch, fake_dns):
        import csv
        import io
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?export=csv&resolve=1")
        rows = list(csv.reader(io.StringIO(r.content.decode())))
        assert rows[0][:5] == ["MAC", "Vendor", "IP Addresses", "Hostname", "Device"]
        h1 = next(x for x in rows if x[0] == "00:00:0c:00:00:01")
        assert h1[3] == "printer-1.example.com" and h1[5] == "1/1/3"
        assert fake_dns["max_ips"] > 100                # export resolves every row

    def test_xlsx_hostname_column(self, auth_client, device, switch, fake_dns):
        _seed_hosts(device, switch)
        cells = _xlsx_cells(auth_client.get("/api/network/hosts/?export=xlsx&resolve=1&q=00:00:0c:00:00:01").content)
        assert cells["D1"] == ("s", "Hostname") and cells["F1"] == ("s", "Port")
        assert cells["D2"] == ("s", "printer-1.example.com")
        assert cells["F2"] == ("s", "1/1/3") and cells["G2"] == ("n", "30")

    def test_resolver_failure_is_blank_not_error(self, auth_client, device, switch, monkeypatch):
        def boom(ips, max_ips=100):
            raise OSError("resolver down")
        monkeypatch.setattr("apps.flows.dns.resolve_ips", boom)
        _seed_hosts(device, switch)
        r = auth_client.get("/api/network/hosts/?resolve=1")
        assert r.status_code == 200 and all(h["hostname"] == "" for h in r.data["results"])


class TestUpdateMacVendors:
    def test_bundled_load_offline(self, db):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        call_command("update_mac_vendors")   # no network
        assert MACVendor.objects.count() > 30000
        assert "Cisco" in MACVendor.objects.get(oui="00:00:0c").vendor

    def test_refresh_falls_back_when_download_fails(self, db, monkeypatch):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor

        def _boom(*a, **k):
            raise RuntimeError("no internet (air-gapped)")
        import requests
        monkeypatch.setattr(requests, "get", _boom)

        call_command("update_mac_vendors", refresh=True)   # download fails → bundled fallback
        # Table is populated from the bundled snapshot, never wiped/skipped.
        assert MACVendor.objects.count() > 30000
        assert MACVendor.objects.filter(oui="00:00:0c").exists()

    def test_file_load_compact_header(self, db, tmp_path):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        p = tmp_path / "custom.csv"
        p.write_text("oui,organization\n11:22:33,Custom Vendor Inc\n")
        call_command("update_mac_vendors", file=str(p))
        assert MACVendor.objects.get(oui="11:22:33").vendor == "Custom Vendor Inc"

    def test_file_load_ieee_header(self, db, tmp_path):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        p = tmp_path / "ieee.csv"
        p.write_text("Registry,Assignment,Organization Name,Address\n"
                     "MA-L,445566,IEEE Style Corp,Somewhere\n")
        call_command("update_mac_vendors", file=str(p))
        assert MACVendor.objects.get(oui="44:55:66").vendor == "IEEE Style Corp"
