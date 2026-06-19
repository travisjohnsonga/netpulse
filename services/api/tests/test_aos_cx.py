"""
AOS-CX enrichment — Stage 1 (REST API client + system enrichment).

Covers apps.devices.aos_cx_client.AOSCXClient and the AOS-CX paths added to
apps.devices.enrich (REST-first system enrichment + SNMP sysDescr fallback).
"""
import pytest

from apps.credentials.models import CredentialProfile
from apps.devices import enrich
from apps.devices.aos_cx_client import AOSCXClient
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── fake HTTP plumbing (no network) ─────────────────────────────────────────────

class _FakeResp:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeCookies:
    def __init__(self, data):
        self._data = data

    def get_dict(self):
        return dict(self._data)


class _FakeSession:
    """Stand-in for requests.Session that returns canned payloads."""

    def __init__(self, *, get_json=None, post_status=200, cookies=None):
        self._get_json = get_json or {}
        self._post_status = post_status
        self.cookies = _FakeCookies(cookies or {})
        self.verify = True
        self.posts = []
        self.closed = False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeResp(status=self._post_status)

    def get(self, url, **kwargs):
        # url is ".../rest/<version>/<resource>"; key the canned map by <resource>.
        after_rest = url.split("/rest/", 1)[-1]
        path = after_rest.split("/", 1)[1] if "/" in after_rest else after_rest
        return _FakeResp(json_data=self._get_json.get(path, self._get_json))

    def close(self):
        self.closed = True


_SYSTEM_PAYLOAD = {
    "hostname": "core-sw-1",
    "software_version": "FL.10.10.1010",
    "hardware_info": {"product_name": "Aruba6300M-48G-Class4PoEP-4SFP56"},
    "serial_number": "SG12345678",
}


# ── client ──────────────────────────────────────────────────────────────────────

class TestAOSCXClient:
    def test_aos_cx_rest_client_login(self):
        client = AOSCXClient("10.0.0.5")
        assert client.base_url == "https://10.0.0.5/rest/v10.09"
        assert client.verify_ssl is False
        assert client.timeout == 10
        client._session = _FakeSession(cookies={"sessionId": "abc123"})

        cookies = client.login("admin", "secret")

        assert cookies == {"sessionId": "abc123"}
        assert client._logged_in is True
        # credentials posted as form data to the login endpoint
        url, kwargs = client._session.posts[0]
        assert url.endswith("/rest/v10.09/login")
        assert kwargs["data"] == {"username": "admin", "password": "secret"}

    def test_aos_cx_login_raises_on_http_error(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(post_status=401)
        with pytest.raises(RuntimeError):
            client.login("admin", "wrong")
        assert client._logged_in is False

    def test_aos_cx_system_info_parse(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system": _SYSTEM_PAYLOAD})

        info = client.get_system()

        assert info["hostname"] == "core-sw-1"
        assert info["version"] == "FL.10.10.1010"
        assert info["model"] == "Aruba6300M-48G-Class4PoEP-4SFP56"
        assert info["serial"] == "SG12345678"
        assert info["raw"] == _SYSTEM_PAYLOAD

    def test_aos_cx_context_manager_logs_out(self):
        client = AOSCXClient("10.0.0.5")
        session = _FakeSession()
        client._session = session
        with client as c:
            c._logged_in = True
        # logout POSTed + session closed on exit
        assert any(url.endswith("/logout") for url, _ in session.posts)
        assert session.closed is True
        assert client._logged_in is False

    def test_aos_cx_interface_parse(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/interfaces": {
            "1/1/1": {"name": "1/1/1", "type": "system", "admin_state": "up",
                      "link_state": "up", "ip4_address": "10.0.0.5/24"},
            "1/1/2": {"name": "1/1/2", "type": "system", "admin_state": "down",
                      "link_state": "down"},
        }})
        ifaces = client.get_interfaces()
        by_name = {i["name"]: i for i in ifaces}
        assert by_name["1/1/1"]["link_state"] == "up"
        assert by_name["1/1/1"]["ip"] == "10.0.0.5/24"
        assert by_name["1/1/2"]["admin_state"] == "down"
        assert by_name["1/1/2"]["ip"] == ""

    def test_aos_cx_lldp_neighbor_parse_full(self):
        """Every advertised TLV is captured from the neighbor_info block."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/lldp_neighbors_info": {
            "1/1/1": {
                "port": "1/1/1",
                "neighbor_info": {
                    "chassis_id": "aa:bb:cc:dd:ee:ff",
                    "chassis_id_subtype": "link_local_addr",
                    "chassis_name": "spine-1",
                    "chassis_description": "ArubaOS-CX 10.10, Aruba8325",
                    "port_id": "1/1/24",
                    "port_description": "to leaf-1",
                    "mgmt_ip_list": "10.0.0.9, fe80::1",
                    "chassis_capability_available": {"bridge": True, "router": True,
                                                     "wlan-access-point": False},
                },
            },
        }})
        nb = client.get_lldp_neighbors()[0]
        assert nb["local_port"] == "1/1/1"
        assert nb["neighbor_hostname"] == "spine-1"
        assert nb["neighbor_port"] == "1/1/24"
        assert nb["neighbor_port_description"] == "to leaf-1"
        assert nb["neighbor_mgmt_ip"] == "10.0.0.9"          # first of the list
        assert nb["chassis_id"] == "aa:bb:cc:dd:ee:ff"
        assert nb["chassis_id_type"] == "link_local_addr"
        assert nb["system_description"] == "ArubaOS-CX 10.10, Aruba8325"
        # capabilities returned raw (dict) — normalised downstream
        assert nb["capabilities"] == {"bridge": True, "router": True,
                                      "wlan-access-point": False}

    def test_aos_cx_lldp_mgmt_ip_falls_back_to_chassis_only_when_ip(self):
        """chassis_id is used for mgmt IP only when it actually looks like one."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/lldp_neighbors_info": {
            "1/1/2": {"port": "1/1/2", "neighbor_info": {
                "chassis_id": "11:22:33:44:55:66"}},          # a MAC → not an IP
            "1/1/3": {"port": "1/1/3", "neighbor_info": {
                "chassis_id": "192.0.2.7"}},                  # an IP → usable
        }})
        by = {n["local_port"]: n for n in client.get_lldp_neighbors()}
        assert by["1/1/2"]["neighbor_mgmt_ip"] == ""
        assert by["1/1/3"]["neighbor_mgmt_ip"] == "192.0.2.7"

    def test_aos_cx_lldp_mgmt_ip_rejects_mac_in_list(self):
        """A MAC advertised in mgmt_ip_list must never become the mgmt IP."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/lldp_neighbors_info": {
            # neighbour advertises only a MAC where an IP belongs
            "1/1/4": {"port": "1/1/4", "neighbor_info": {
                "mgmt_ip_list": "40:5b:7f:66:05:e1",
                "chassis_id": "40:5b:7f:66:05:e1"}},
            # a real IP after a MAC in the list → the IP wins
            "1/1/5": {"port": "1/1/5", "neighbor_info": {
                "mgmt_ip_list": "40:5b:7f:66:05:e1, 10.150.0.15"}},
        }})
        by = {n["local_port"]: n for n in client.get_lldp_neighbors()}
        assert by["1/1/4"]["neighbor_mgmt_ip"] == ""
        assert by["1/1/5"]["neighbor_mgmt_ip"] == "10.150.0.15"

    def test_aos_cx_interface_stats_captured(self):
        """depth-2 interfaces carry statistics + rate_statistics + mtu."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/interfaces": {
            "1/1/1": {
                "name": "1/1/1", "admin_state": "up", "link_state": "up",
                "link_speed": 10_000_000_000, "mtu": 1500, "description": "uplink",
                "statistics": {"rx_bytes": 1000, "tx_bytes": 2000,
                               "rx_packets": 10, "tx_packets": 20,
                               "if_in_errors": 1, "fe_if_in_discard_packets": 3},
                "rate_statistics": {"rx_bytes_per_second": 100, "tx_bytes_per_second": 200,
                                    "rx_packets_per_second": 5, "tx_packets_per_second": 6},
            },
        }})
        from apps.devices.aos_cx_client import interface_counters
        iface = client.get_interfaces()[0]
        assert iface["mtu"] == 1500
        assert iface["speed_mbps"] == 10_000
        c = interface_counters(iface)
        assert c["rx_bytes"] == 1000 and c["tx_bytes"] == 2000
        assert c["rx_packets"] == 10 and c["tx_packets"] == 20
        assert c["rx_errors"] == 1 and c["rx_discards"] == 3
        assert c["rx_bps"] == 100 and c["tx_bps"] == 200
        assert c["rx_pps"] == 5 and c["tx_pps"] == 6
        assert c["tx_errors"] is None   # firmware doesn't expose if_out_errors

    def test_aos_cx_interface_counters_falls_back_to_hc_keys(self):
        """rx/tx bytes fall back to the if_hc_* high-capacity counters."""
        from apps.devices.aos_cx_client import interface_counters
        c = interface_counters({"statistics": {"if_hc_in_bytes": 42, "if_hc_out_bytes": 43}})
        assert c["rx_bytes"] == 42 and c["tx_bytes"] == 43

    def test_aos_cx_system_info_serial_and_base_mac(self):
        """get_system_info reads serial/base-MAC off the chassis product_info."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={
            "system": {"hostname": "wco2-mdf-crt-01", "platform_name": "6300",
                       "software_version": "FL.10.13.1160"},
            "system/subsystems": {"chassis,1": "/rest/v10.09/system/subsystems/chassis,1",
                                  "fan_tray,1/1": "/uri"},
            "system/subsystems/chassis,1": {"product_info": {
                "base_mac_address": "9c:37:08:25:f3:40", "serial_number": "SG44LMP040",
                "product_name": "6300M 24SFP+ 4SFP56 Swch", "part_number": "JL658A"}},
        })
        info = client.get_system_info()
        assert info["hostname"] == "wco2-mdf-crt-01"
        assert info["os_version"] == "FL.10.13.1160"
        assert info["model"] == "6300"
        assert info["serial_number"] == "SG44LMP040"
        assert info["base_mac"] == "9c:37:08:25:f3:40"
        assert info["part_number"] == "JL658A"

    def test_aos_cx_arp_table_across_vrfs(self):
        """ARP comes from per-VRF neighbors; IPv6 ND is skipped, MAC kept raw."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={
            "system/vrfs": {"default": "/uri", "mgmt": "/uri"},
            "system/vrfs/default/neighbors": {
                "10.150.0.1,vlan1": {
                    "address_family": "ipv4", "from": "dynamic", "ip_address": "10.150.0.1",
                    "mac": "1a:c2:41:2c:0b:0c", "state": "reachable",
                    "port": {"vlan1": "/rest/v10.09/system/interfaces/vlan1"}},
                "fe80::1,vlan1": {  # IPv6 ND — must be skipped
                    "address_family": "ipv6", "ip_address": "fe80::1",
                    "mac": "1a:c2:41:2c:0b:0d"},
                "10.150.0.2,vlan5": {  # static
                    "address_family": "ipv4", "from": "static", "ip_address": "10.150.0.2",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "port": {"vlan5": "/rest/v10.09/system/interfaces/vlan5"}},
            },
            "system/vrfs/mgmt/neighbors": {},
        })
        rows = client.get_arp_table()
        by_ip = {r["ip_address"]: r for r in rows}
        assert set(by_ip) == {"10.150.0.1", "10.150.0.2"}   # IPv6 excluded
        assert by_ip["10.150.0.1"]["mac_address"] == "1a:c2:41:2c:0b:0c"  # raw
        assert by_ip["10.150.0.1"]["interface"] == "vlan1"
        assert by_ip["10.150.0.1"]["vlan"] == 1
        assert by_ip["10.150.0.1"]["entry_type"] == "dynamic"
        assert by_ip["10.150.0.2"]["entry_type"] == "static"
        assert by_ip["10.150.0.2"]["vlan"] == 5

    def test_aos_cx_mac_table_from_vlans(self):
        """MAC table is read per-VLAN (GET /system/vlans/<id>/macs)."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={
            # depth-1 VLAN list: {<vlan_id>: URI}
            "system/vlans": {
                "1": "/rest/v10.09/system/vlans/1",
                "5": "/rest/v10.09/system/vlans/5",
            },
            "system/vlans/1/macs": {
                "dynamic,00:09:01:12:a6:c3": {
                    "mac_addr": "00:09:01:12:a6:c3", "from": "dynamic",
                    "port": {"lag2": "/rest/v10.09/system/interfaces/lag2"}},
            },
            "system/vlans/5/macs": {
                "static,aa:bb:cc:00:11:22": {
                    "mac_addr": "aa:bb:cc:00:11:22", "from": "static",
                    "port": {"1/1/3": "/rest/v10.09/system/interfaces/1%2F1%2F3"}},
            },
        })
        rows = client.get_mac_table()
        by_mac = {r["mac_address"]: r for r in rows}
        assert by_mac["00:09:01:12:a6:c3"]["vlan"] == 1
        assert by_mac["00:09:01:12:a6:c3"]["interface"] == "lag2"
        assert by_mac["00:09:01:12:a6:c3"]["entry_type"] == "dynamic"
        assert by_mac["aa:bb:cc:00:11:22"]["vlan"] == 5
        assert by_mac["aa:bb:cc:00:11:22"]["interface"] == "1/1/3"
        assert by_mac["aa:bb:cc:00:11:22"]["entry_type"] == "static"

    def test_aos_cx_environment_sensors(self):
        """Temp/fan/PSU sensors aggregate across subsystems; temp → °C."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/subsystems": {
            "line_card,1/1": {"temp_sensors": {
                "1/1-ASIC": {"name": "1/1-ASIC", "location": "asic",
                             "temperature": 61375, "status": "normal"}}},
            "fan_tray,1/1": {"fans": {
                "Tray-1/1/1": {"name": "Tray-1/1/1", "rpm": 6941, "speed": "slow",
                               "status": "ok"}}},
            "chassis,1": {"power_supplies": {
                "1/1": {"name": "1/1", "status": "ok",
                        "characteristics": {"instantaneous_power": 27, "maximum_power": 250},
                        "identity": {"product_name": "JL085A", "serial_number": "TH46"}}}},
        }})
        env = client.get_environment()
        assert env["temperatures"][0]["temperature_c"] == 61.375
        assert env["temperatures"][0]["status"] == "normal"
        assert env["fans"][0]["rpm"] == 6941 and env["fans"][0]["status"] == "ok"
        psu = env["power_supplies"][0]
        assert psu["status"] == "ok" and psu["maximum_power"] == 250
        assert psu["model"] == "JL085A"

    def test_aos_cx_vlans(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/vlans": {
            "1": {"id": 1, "name": "DEFAULT_VLAN_1", "admin": "up",
                  "oper_state": "up", "type": "static"},
            "20": {"id": 20, "name": "DATA", "admin": "up", "oper_state": "down"},
        }})
        by_id = {v["id"]: v for v in client.get_vlans()}
        assert by_id[1]["name"] == "DEFAULT_VLAN_1" and by_id[1]["oper_state"] == "up"
        assert by_id[20]["name"] == "DATA" and by_id[20]["oper_state"] == "down"

    def test_aos_cx_interface_lacp_bond_and_vlan(self):
        """get_interfaces exposes LACP/LAG state and interface VLAN membership."""
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/interfaces": {
            "1/1/1": {"name": "1/1/1", "vlan_mode": "access",
                      "vlan_tag": {"1": "/rest/v10.09/system/vlans/1"},
                      "lacp_status": {"actor_key": "1", "actor_state": "Activ:1"},
                      "bond_status": {"state": "up"}},
            "lag256": {"name": "lag256", "vlan_mode": "native-untagged",
                       "vlan_trunks": {"1": "/uri", "100": "/uri"},
                       "bond_status": {"state": "up", "bond_speed": 20_000_000_000}},
        }})
        by = {i["name"]: i for i in client.get_interfaces()}
        assert by["1/1/1"]["vlan_mode"] == "access"
        assert by["1/1/1"]["vlan_tag"] == "1"
        assert by["1/1/1"]["lacp_status"]["actor_key"] == "1"
        assert by["1/1/1"]["bond_status"]["state"] == "up"
        assert sorted(by["lag256"]["vlan_trunks"]) == ["1", "100"]
        assert by["lag256"]["bond_status"]["bond_speed"] == 20_000_000_000

    def test_aos_cx_get_interface_stats_single_port(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(get_json={"system/interfaces/1%2F1%2F1": {
            "name": "1/1/1",
            "statistics": {"rx_bytes": 100, "tx_bytes": 200},
            "rate_statistics": {"rx_bytes_per_second": 7},
        }})
        c = client.get_interface_stats("1/1/1")
        assert c["rx_bytes"] == 100 and c["tx_bytes"] == 200 and c["rx_bps"] == 7

    def test_aos_cx_poe_status_skips_non_poe_ports(self):
        """PoE-capable ports return data; SFP+ ports 404 and are skipped."""
        routes = {
            "system/interfaces": (200, {"1/1/1": "/uri", "1/1/2": "/uri", "lag1": "/uri"}),
            "system/interfaces/1%2F1%2F1/poe_interface": (200, {
                "config": {"admin_disable": False},
                "status": {"poe_oper_status": "delivering", "pd_class_actual": "class3",
                           "power_drawn_in_watts": 7}}),
            "system/interfaces/1%2F1%2F2/poe_interface": (404, {}),
        }

        class _Resp:
            def __init__(self, status, data):
                self.status_code, self._data = status, data

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def json(self):
                return self._data

        class _RouteSession:
            verify = True

            def get(self, url, **kwargs):
                path = url.split("/rest/v10.09/", 1)[-1]
                return _Resp(*routes.get(path, (404, {})))

            def close(self):
                pass

        client = AOSCXClient("10.0.0.5")
        client._session = _RouteSession()
        poe = client.get_poe_status()
        assert len(poe) == 1                       # only 1/1/1 has PoE (lag1 not physical)
        assert poe[0]["port"] == "1/1/1"
        assert poe[0]["poe_status"] == "delivering"
        assert poe[0]["pd_class"] == "class3"
        assert poe[0]["power_drawn"] == 7

    def test_aos_cx_lldp_falls_back_to_interface_walk(self):
        """FL.10.13 returns HTTP 400 for lldp_neighbors_info → walk interfaces.

        The fallback reads ``system/interfaces`` at depth 4 where each port's
        ``lldp_neighbors`` child is expanded inline to ``{key: detail}``; the
        detail carries ``chassis_id``/``port_id`` at the top level and the rest
        of the TLVs under ``neighbor_info`` (shape verified on an HPE 6100).
        """
        routes = {
            # aggregated endpoint is gone on FL.10.13 → 400 forces the fallback
            "system/lldp_neighbors_info": (400, {}),
            "system/interfaces": (200, {
                "1/1/1": {"name": "1/1/1", "lldp_neighbors": {}},   # no neighbour
                "1/1/49": {"name": "1/1/49", "lldp_neighbors": {
                    "14:ab:ec:fb:e9:c0,1/1/49": {
                        "chassis_id": "14:ab:ec:fb:e9:c0",
                        "port_id": "1/1/49",
                        "neighbor_info": {
                            "chassis_name": "wco2-mdf-asw-01",
                            "chassis_description": "HPE ANW, ArubaOS-CX",
                            "chassis_id_subtype": "link_local_addr",
                            "port_description": "Connected to the Core Switch",
                            "mgmt_ip_list": "10.150.0.12",
                            "chassis_capability_available": "Bridge, Router",
                            "vlan_id_list": "1,10,20,30",
                        },
                    },
                }},
            }),
        }

        class _Resp:
            def __init__(self, status, data):
                self.status_code, self._data = status, data

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def json(self):
                return self._data

        class _RouteSession:
            verify = True

            def get(self, url, **kwargs):
                path = url.split("/rest/v10.09/", 1)[-1]
                return _Resp(*routes.get(path, (200, {})))

            def close(self):
                pass

        client = AOSCXClient("10.0.0.5")
        client._session = _RouteSession()

        nbrs = client.get_lldp_neighbors()

        assert len(nbrs) == 1                      # only 1/1/49 has a neighbour
        nb = nbrs[0]
        assert nb["local_port"] == "1/1/49"
        assert nb["neighbor_hostname"] == "wco2-mdf-asw-01"
        assert nb["neighbor_port"] == "1/1/49"
        assert nb["neighbor_port_description"] == "Connected to the Core Switch"
        assert nb["neighbor_mgmt_ip"] == "10.150.0.12"
        assert nb["chassis_id"] == "14:ab:ec:fb:e9:c0"
        assert nb["chassis_id_type"] == "link_local_addr"
        assert nb["system_description"] == "HPE ANW, ArubaOS-CX"
        # capabilities returned raw (delimited string) — normalised downstream
        assert nb["capabilities"] == "Bridge, Router"


# ── running-config collection (config backup) ───────────────────────────────────

class TestAOSCXConfigCollection:
    """The AOS-CX path in apps.compliance.collector — SSH ``show running-config``
    first (complete CLI config), REST fallback (partial — vlans/interfaces).
    Netmiko is deliberately avoided: its interactive send_command hangs on the
    AOS-CX ``--More--`` pager; the exec channel is pager-immune."""

    def test_get_running_config_uses_fullconfigs_endpoint(self):
        client = AOSCXClient("10.0.0.5")
        client._session = _FakeSession(
            get_json={"fullconfigs/running-config": {"System": {"hostname": "cx"}}})
        cfg = client.get_running_config()
        assert cfg == {"System": {"hostname": "cx"}}

    @pytest.mark.django_db
    def test_rest_is_fallback_when_ssh_fails(self, monkeypatch):
        """When SSH (primary) fails, _fetch_running_config falls back to the REST
        collector and serialises the JSON document to stable text."""
        from apps.compliance import collector

        profile = CredentialProfile.objects.create(
            name="cx", ssh_enabled=True, ssh_username="admin")
        device = Device.objects.create(
            hostname="cx", ip_address="10.0.0.5", management_ip="10.0.0.5",
            platform="aos_cx", credential_profile=profile)

        # SSH is primary now; force it to fail so the REST fallback is exercised.
        monkeypatch.setattr(collector, "_fetch_aos_cx_via_ssh",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ssh")))

        captured = {}

        class _Client:
            def __init__(self, host, **kw):
                captured["host"] = host

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, user, pw):
                captured["creds"] = (user, pw)

            def get_running_config(self):
                return {"b": 2, "a": 1}

        monkeypatch.setattr(
            "apps.devices.aos_cx_client.AOSCXClient", _Client)

        out = collector._fetch_running_config(device, {"ssh_password": "secret"})

        assert captured["host"] == "10.0.0.5"
        assert captured["creds"] == ("admin", "secret")
        # JSON serialised with sorted keys → stable for hashing/diffing
        assert out == '{\n  "a": 1,\n  "b": 2\n}'

    @pytest.mark.django_db
    def test_ssh_is_primary_show_running_config(self, monkeypatch):
        """SSH 'show running-config' (paramiko exec_command) is the PRIMARY path;
        REST is never reached when SSH succeeds (REST mocked to boom proves it)."""
        from apps.compliance import collector

        profile = CredentialProfile.objects.create(
            name="cx", ssh_enabled=True, ssh_username="admin")
        device = Device.objects.create(
            hostname="cx", ip_address="10.0.0.5", management_ip="10.0.0.5",
            platform="aos_cx", credential_profile=profile)

        def _boom(*a, **k):
            raise RuntimeError("REST should not be called when SSH succeeds")

        monkeypatch.setattr(collector, "_fetch_aos_cx_via_rest", _boom)

        connect_kwargs = {}
        exec_calls = []

        class _Stdout:
            def read(self):
                return b"hostname cx\ninterface 1/1/1\n"

        class _SSH:
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, host, **kwargs):
                connect_kwargs["host"] = host
                connect_kwargs.update(kwargs)

            def exec_command(self, cmd, timeout=None):
                exec_calls.append((cmd, timeout))
                return (None, _Stdout(), None)

            def close(self):
                connect_kwargs["closed"] = True

        import paramiko
        monkeypatch.setattr(paramiko, "SSHClient", lambda: _SSH())

        out = collector._fetch_running_config(device, {"ssh_password": "secret"})

        # _strip_aos_cx_preamble re-joins lines (the trailing newline is dropped);
        # the config begins at the first real config line.
        assert out == "hostname cx\ninterface 1/1/1"
        assert connect_kwargs["host"] == "10.0.0.5"
        assert connect_kwargs["username"] == "admin"
        assert connect_kwargs["password"] == "secret"
        assert connect_kwargs["timeout"] == 15           # bounded connect → no hang
        assert connect_kwargs["look_for_keys"] is False
        assert connect_kwargs["allow_agent"] is False
        assert connect_kwargs["closed"] is True
        assert exec_calls == [("show running-config", 60)]

    @pytest.mark.django_db
    def test_ssh_exec_empty_config_raises(self, monkeypatch):
        from apps.compliance import collector

        profile = CredentialProfile.objects.create(
            name="cx", ssh_enabled=True, ssh_username="admin")
        device = Device.objects.create(
            hostname="cx", ip_address="10.0.0.5", management_ip="10.0.0.5",
            platform="aos_cx", credential_profile=profile)

        class _Stdout:
            def read(self):
                return b"   \n"

        class _SSH:
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, host, **kwargs):
                pass

            def exec_command(self, cmd, timeout=None):
                return (None, _Stdout(), None)

            def close(self):
                pass

        import paramiko
        monkeypatch.setattr(paramiko, "SSHClient", lambda: _SSH())

        with pytest.raises(ValueError):
            collector._fetch_aos_cx_via_ssh(device, profile, {"ssh_password": "x"})

    def test_strip_preamble_drops_non_config_header(self):
        from apps.compliance import collector

        raw = ("Current configuration:\n"
               "!\n!Version AOS-CX PL.10.16\nhostname cx\n"
               "interface 1/1/1\n    no shutdown\n")
        out = collector._strip_aos_cx_preamble(raw)
        assert out.startswith("!\n!Version")            # 'Current configuration:' dropped
        assert "Current configuration:" not in out
        assert "hostname cx" in out and "interface 1/1/1" in out

    def test_strip_preamble_noop_when_no_header(self):
        from apps.compliance import collector
        cfg = "hostname cx\ninterface 1/1/1\n"
        assert collector._strip_aos_cx_preamble(cfg) == "hostname cx\ninterface 1/1/1"


# ── SNMP sysDescr fallback parsing ──────────────────────────────────────────────

class TestAOSCXSysDescr:
    def test_aos_cx_sysdescr_parsing(self):
        updates: dict = {}
        res = {
            enrich._OID_SYS_DESCR: "ArubaOS-CX 10.10.1010, Aruba6300M Switch",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.47196.4.1.1.3.8",
        }
        enrich._parse_snmp(res, updates)
        assert updates["os_version"] == "10.10.1010"
        assert updates["platform"] == "aos_cx"
        assert updates["vendor"] == "aruba"
        assert updates["model"] == "Aruba6300M"


# ── enrichment pipeline ─────────────────────────────────────────────────────────

@pytest.fixture
def aos_profile():
    return CredentialProfile.objects.create(
        name="aoscx", ssh_enabled=True, ssh_username="admin")


@pytest.fixture
def aos_device(aos_profile):
    return Device.objects.create(
        hostname="cx", ip_address="10.0.0.5", management_ip="10.0.0.5",
        platform="aos_cx", credential_profile=aos_profile)


def _no_network(monkeypatch):
    monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: {})
    monkeypatch.setattr(enrich, "_ssh_collect", lambda ip, p, s: {})
    monkeypatch.setattr(enrich, "_discover_interfaces", lambda d: ([], 0, 0))
    monkeypatch.setattr(enrich, "_discover_lldp", lambda d, i=None: 0)
    monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: None)
    monkeypatch.setattr(enrich, "_collect_config", lambda d: None)


class _FakeClient:
    """Context-manager stand-in for AOSCXClient (canned interfaces + neighbours)."""

    def __init__(self, ifaces, neighbors):
        self._ifaces = ifaces
        self._neighbors = neighbors

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, username, password):
        return {}

    def get_interfaces(self):
        return self._ifaces

    def get_lldp_neighbors(self):
        return self._neighbors


class TestAOSCXInterfaceDiscovery:
    def test_aos_cx_interface_discovery(self, aos_device, aos_profile, monkeypatch):
        from apps.telemetry import discovery

        aos_profile.vault_path = "secret/devices/cx"
        aos_profile.save()
        monkeypatch.setattr(discovery.vault, "read_secret", lambda p: {"ssh_password": "pw"})
        ifaces = [
            {"name": "1/1/1", "type": "system", "admin_state": "up", "link_state": "up",
             "ip": "", "description": "uplink", "speed_mbps": 1000},
            {"name": "1/1/2", "type": "system", "admin_state": "down", "link_state": "down",
             "ip": "", "description": "", "speed_mbps": None},
        ]
        neighbors = [{"local_port": "1/1/1", "neighbor_hostname": "spine-1",
                      "neighbor_port": "1/1/24", "neighbor_mgmt_ip": "10.0.0.9"}]
        monkeypatch.setattr("apps.devices.aos_cx_client.AOSCXClient",
                            lambda host, **kw: _FakeClient(ifaces, neighbors))

        rows = discovery.discover_interfaces(aos_device)
        by = {r["if_name"]: r for r in rows}

        assert by["1/1/1"]["collection_method"] == "rest"
        assert by["1/1/1"]["lldp_neighbor_hostname"] == "spine-1"
        assert by["1/1/1"]["lldp_neighbor_port"] == "1/1/24"
        assert by["1/1/1"]["lldp_neighbor_mgmt_ip"] == "10.0.0.9"
        assert by["1/1/1"]["if_speed_mbps"] == 1000
        assert by["1/1/1"]["auto_select"] is True    # up + LLDP neighbour
        assert by["1/1/2"]["auto_select"] is False    # down

    def test_aos_cx_interface_discovery_falls_back_to_ssh(self, aos_device, monkeypatch):
        """REST failure (no creds / unreachable) falls through to the SSH path."""
        from apps.telemetry import discovery

        # aos_profile fixture has ssh_enabled=True; REST raises → SSH fallback.
        monkeypatch.setattr(discovery.vault, "read_secret", lambda p: {})

        def _boom(device, profile, creds):
            raise discovery.DiscoveryError("REST down")
        monkeypatch.setattr(discovery, "_discover_via_aos_cx_rest", _boom)
        ssh_rows = [{"if_name": "1/1/1", "if_type": "", "oper_status": "up",
                     "lldp_neighbor_hostname": None}]
        monkeypatch.setattr(discovery, "_discover_via_ssh", lambda d, p, c: list(ssh_rows))

        rows = discovery.discover_interfaces(aos_device)
        assert rows[0]["collection_method"] == "snmp"  # non-REST rows keep the snmp label


class TestAOSCXLLDPDiscovery:
    def test_aos_cx_lldp_discovery(self, aos_device):
        from apps.devices import topology
        from apps.devices.models import Device, TopologyLink

        neighbor = Device.objects.create(
            hostname="spine-1", ip_address="10.0.0.9", management_ip="10.0.0.9",
            platform="aos_cx")
        interfaces = [{
            "if_index": None, "if_name": "1/1/1", "if_description": "",
            "if_speed_mbps": 1000, "if_type": "system", "oper_status": "up",
            "admin_status": "up", "lldp_neighbor_hostname": "spine-1",
            "lldp_neighbor_port": "1/1/24", "lldp_neighbor_desc": "1/1/24",
            "lldp_neighbor_mgmt_ip": "10.0.0.9",
        }]

        found = topology.discover_links(aos_device, interfaces=interfaces)

        assert any(f.get("matched_device_id") == neighbor.id for f in found)
        assert TopologyLink.objects.count() == 1


class TestAOSCXEnrichmentPipeline:
    def test_aos_cx_enrichment_pipeline(self, aos_device, monkeypatch):
        _no_network(monkeypatch)
        # get_system_info() shape: os_version/serial_number + product_name model.
        rest = {"hostname": "core-sw-1", "os_version": "FL.10.10.1010",
                "model": "6300", "product_name": "Aruba6300M-48G",
                "serial_number": "SG12345678", "base_mac": "9c:37:08:25:f3:40",
                "raw": {}}
        monkeypatch.setattr(enrich, "_aos_cx_collect", lambda ip, p, s: rest)
        # If REST succeeds, SNMP must not be consulted.
        monkeypatch.setattr(enrich, "_snmp_collect",
                            lambda ip, p, s: pytest.fail("SNMP should not run when REST succeeds"))

        changed = enrich.enrich_device(aos_device.id)
        aos_device.refresh_from_db()

        assert aos_device.os_version == "FL.10.10.1010"
        assert aos_device.model == "Aruba6300M-48G"
        assert aos_device.serial_number == "SG12345678"
        assert aos_device.hostname == "core-sw-1"
        assert aos_device.vendor == "aruba"
        assert set(changed) >= {"os_version", "model", "serial_number"}

    def test_aos_cx_falls_back_to_snmp_when_rest_fails(self, aos_device, monkeypatch):
        _no_network(monkeypatch)
        monkeypatch.setattr(enrich, "_aos_cx_collect", lambda ip, p, s: {})
        snmp = {
            enrich._OID_SYS_DESCR: "ArubaOS-CX 10.10.1010, Aruba6300M Switch",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.47196.4.1.1.3.8",
        }
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: snmp)

        enrich.enrich_device(aos_device.id)
        aos_device.refresh_from_db()

        assert aos_device.os_version == "10.10.1010"
        assert aos_device.model == "Aruba6300M"
        assert aos_device.vendor == "aruba"
