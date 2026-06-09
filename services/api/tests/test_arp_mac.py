"""ARP/MAC collection: normalization, parsing, persistence, and API."""
import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    from apps.devices.models import Device
    return Device.objects.create(hostname="sw1", ip_address="10.150.0.21",
                                 management_ip="10.150.0.21", platform="aos_cx", status="active")


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
            "10.16.128.129  Static   1A:C2:41:2C:0B:0C  SONICWALL                   X0:V1000   Permanent published\n"
            "10.16.128.135  Dynamic  9C:37:08:25:F3:40  HEWLETT PACKARD ENTERPRISE  X0:V1000   Expires in 10 minutes  10\n"
            "10.16.129.11   Dynamic  D4:A2:CD:13:5C:FB  DELL                        X0:V201    Expires in 2 minutes   2\n"
        )
        out = _parse_sonicwall_arp(sample)
        assert out[0] == {"ip_address": "10.16.128.129", "mac_address": "1a:c2:41:2c:0b:0c",
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
                        b"10.16.128.129  Static   1A:C2:41:2C:0B:0C  SONICWALL                   X0:V1000   Permanent published\r\n"
                        b"10.16.128.135  Dynamic  9C:37:08:25:F3:40  HEWLETT PACKARD ENTERPRISE  X0:V1000   Expires in 10 minutes  10\r\n"
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
        assert {e["ip_address"] for e in entries} == {"10.16.128.129", "10.16.128.135"}
        assert entries[0]["entry_type"] == "static"

    def test_fortios_arp_regex(self):
        from apps.arp_mac.collector import _parse_fortios_arp
        out = _parse_fortios_arp(
            "Address           Age(min)   Hardware Addr      Interface\n"
            "10.150.0.1        0          aa:bb:cc:dd:ee:ff  port1\n")
        assert out == [{"ip_address": "10.150.0.1", "mac_address": "aa:bb:cc:dd:ee:ff",
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
        arp = [{"ip_address": "10.150.0.1", "mac_address": "1A:C2:41:2C:0B:0C",
                "interface": "vlan1", "vlan": 1, "age_minutes": None,
                "protocol": "Internet", "entry_type": "dynamic"}]
        mac = [{"mac_address": "00:09:01:12:A6:C3", "vlan": 1,
                "interface": "lag2", "entry_type": "dynamic"}]
        monkeypatch.setattr("apps.devices.aos_cx_client.AOSCXClient",
                            lambda host, **kw: self._FakeRestClient(arp, mac))

        arp_out, mac_out = collector.collect_arp_mac(
            device, {"ssh_password": "pw"}, "travis-admin")

        assert arp_out[0]["ip_address"] == "10.150.0.1"
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
