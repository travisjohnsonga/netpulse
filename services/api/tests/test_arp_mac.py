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
                           "Type", "Last Seen"]
        assert len(rows) == 1 + 3
        h1 = next(x for x in rows if x[0] == "00:00:0c:00:00:01")
        # Port "1/1/3" is wrapped so Excel shows text, not a date.
        assert h1[1:7] == ["Cisco Systems, Inc", "10.0.0.30", "sw2", '="1/1/3"', "30", "static"]

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

    @pytest.mark.parametrize("port,expected", [
        ("1/1/16", '="1/1/16"'), ("1/1/51", '="1/1/51"'), ("17", '="17"'), ("1-2", '="1-2"'),
        ("ge-0/0/1", "ge-0/0/1"), ("ae9", "ae9"), ("lag10", "lag10"), ("Gi0/1", "Gi0/1"),
        ("", ""),
    ])
    def test_port_kept_as_text_in_excel(self, auth_client, switch, port, expected):
        from apps.arp_mac.models import MACEntry
        MACEntry.objects.create(device=switch, mac_address="00:00:5e:00:53:0a", interface=port)
        rows = self._csv(auth_client.get("/api/network/hosts/?export=csv"))
        assert rows[1][4] == expected

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/network/hosts/?export=csv").status_code == 401


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


class TestVendorPrefixes:
    def test_longest_prefix_wins(self):
        from apps.arp_mac.models import MACVendor
        from apps.arp_mac.vendors import vendor_map
        MACVendor.objects.create(oui="00:00:5e", vendor="IEEE Registration Authority")
        MACVendor.objects.create(oui="00:00:5e:0", vendor="Medium Block Co")
        MACVendor.objects.create(oui="00:00:5e:00:5", vendor="Small Block Co")
        got = vendor_map(["00:00:5e:00:53:01", "00:00:5e:01:00:01", "00:00:5e:90:00:01"])
        assert got == {"00:00:5e:00:53:01": "Small Block Co",
                       "00:00:5e:01:00:01": "Medium Block Co",
                       "00:00:5e:90:00:01": "IEEE Registration Authority"}

    def test_locally_administered_is_labelled(self):
        from apps.arp_mac.normalize import LOCALLY_ADMINISTERED
        from apps.arp_mac.vendors import vendor_map
        got = vendor_map(["02:00:5e:00:53:01", "00:00:5e:00:53:02"])
        assert got["02:00:5e:00:53:01"] == LOCALLY_ADMINISTERED
        assert got["00:00:5e:00:53:02"] == ""   # globally unique, just unknown

    def test_registry_entry_beats_locally_administered_label(self):
        from apps.arp_mac.models import MACVendor
        from apps.arp_mac.vendors import vendor_for
        MACVendor.objects.create(oui="aa:bb:cc", vendor="Acme Networks")
        assert vendor_for("aa:bb:cc:00:00:01") == "Acme Networks"

    def test_mac_vendor_endpoint_uses_long_prefixes(self, auth_client):
        from apps.arp_mac.models import MACVendor
        MACVendor.objects.create(oui="00:00:5e", vendor="IEEE Registration Authority")
        MACVendor.objects.create(oui="00:00:5e:00:5", vendor="Small Block Co")
        r = auth_client.get("/api/network/mac-vendor/00-00-5E-00-53-01/")
        assert r.status_code == 200
        assert r.data == {"mac": "00:00:5e:00:53:01", "oui": "00:00:5e", "vendor": "Small Block Co"}

    def test_prefix_from_assignment(self):
        from apps.arp_mac.normalize import prefix_from_assignment
        assert prefix_from_assignment("00005E") == "00:00:5e"
        assert prefix_from_assignment("00005E0") == "00:00:5e:0"
        assert prefix_from_assignment("00005E005") == "00:00:5e:00:5"
        assert prefix_from_assignment("00005") == ""
        assert prefix_from_assignment("ZZ005E") == ""


class TestUpdateMacVendorsRegistries:
    def test_bundled_snapshot_has_all_block_sizes(self):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        call_command("update_mac_vendors")
        lengths = {len(o) for o in MACVendor.objects.values_list("oui", flat=True)}
        assert lengths == {8, 10, 13}

    def test_file_load_ieee_small_blocks(self, tmp_path):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        p = tmp_path / "ieee.csv"
        p.write_text("Registry,Assignment,Organization Name,Organization Address\n"
                     "MA-M,00005E0,Medium Block Co,Somewhere\n"
                     "MA-S,00005E005,Small Block Co,Somewhere\n")
        call_command("update_mac_vendors", file=str(p))
        assert MACVendor.objects.get(oui="00:00:5e:0").vendor == "Medium Block Co"
        assert MACVendor.objects.get(oui="00:00:5e:00:5").vendor == "Small Block Co"

    def _fake_get(self, monkeypatch, fail=()):
        import requests
        seen = []
        bodies = {
            "oui.csv": "MA-L,00005E,IEEE Registration Authority,x\n",
            "mam.csv": "MA-M,00005E0,Medium Block Co,x\n",
            "oui36.csv": "MA-S,00005E005,Small Block Co,x\n",
            "iab.csv": "IAB,0050C2001,Legacy Block Co,x\n",
        }

        class _Resp:
            def __init__(self, text):
                self.content = ("Registry,Assignment,Organization Name,Organization Address\n"
                                + text).encode()

            def raise_for_status(self):
                pass

        def _get(url, timeout=None, headers=None):
            seen.append((url, headers))
            name = url.rsplit("/", 1)[-1]
            if name in fail:
                raise RuntimeError("down")
            return _Resp(bodies[name])
        monkeypatch.setattr(requests, "get", _get)
        return seen

    def test_refresh_downloads_every_registry_with_product_user_agent(self, monkeypatch):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        seen = self._fake_get(monkeypatch)
        call_command("update_mac_vendors", refresh=True)
        assert [u.rsplit("/", 1)[-1] for u, _ in seen] == ["oui.csv", "mam.csv", "oui36.csv", "iab.csv"]
        # IEEE answers 418 to python-requests' default UA.
        assert all("python-requests" not in h["User-Agent"] for _, h in seen)
        assert set(MACVendor.objects.values_list("oui", flat=True)) == {
            "00:00:5e", "00:00:5e:0", "00:00:5e:00:5", "00:50:c2:00:1"}

    def test_refresh_keeps_bundled_entries_when_small_registry_fails(self, monkeypatch):
        from django.core.management import call_command
        from apps.arp_mac.models import MACVendor
        self._fake_get(monkeypatch, fail={"mam.csv"})
        call_command("update_mac_vendors", refresh=True)
        ouis = set(MACVendor.objects.values_list("oui", flat=True))
        assert not MACVendor.objects.filter(vendor="Medium Block Co").exists()
        assert sum(len(o) == 10 for o in ouis) > 1000   # bundled MA-M kept
        assert MACVendor.objects.get(oui="00:00:5e:00:5").vendor == "Small Block Co"
        assert sum(len(o) == 8 for o in ouis) == 1      # live MA-L used, not the bundled one
