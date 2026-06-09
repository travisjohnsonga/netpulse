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
        rest = {"hostname": "core-sw-1", "version": "FL.10.10.1010",
                "model": "Aruba6300M-48G", "serial": "SG12345678", "raw": {}}
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
