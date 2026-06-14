"""LLDP neighbor persistence, inventory matching, and the undiscovered API."""
import pytest

from apps.devices import lldp, topology
from apps.devices.models import Device, LLDPNeighbor
from apps.telemetry import discovery

pytestmark = pytest.mark.django_db


@pytest.fixture
def devices():
    a = Device.objects.create(hostname="rtr-01", ip_address="10.0.0.1", platform="ios_xe", status="active")
    b = Device.objects.create(hostname="rtr-02", ip_address="10.0.0.2", platform="ios_xe", status="active")
    return a, b


class TestPlatformGuess:
    @pytest.mark.parametrize("desc,expected", [
        ("Cisco IOS Software, IOS-XE Software, CSR1000V", "ios_xe"),
        ("Cisco IOS XR Software", "ios_xr"),
        ("Cisco IOS Software, 7200 Software", "ios"),
        ("Cisco NX-OS(tm) n9000", "nxos"),
        ("Arista Networks EOS version 4.2", "eos"),
        ("ArubaOS-CX VirtualSwitch", "aos_cx"),
        ("Aruba JL658A 6300M", "aos_cx"),
        ("FortiGate-VM64 FortiOS v7.4.3", "fortios"),
        ("Juniper Networks, Inc. JUNOS 21.4", "junos"),
        ("SonicWall SonicOS Enhanced", "sonicwall"),
        ("Palo Alto Networks PAN-OS 11.0", "panos"),
        ("UniFi Switch USW-24-PoE", "unifi_sw"),
        ("", "other"),
        (None, "other"),
        ("Some random vendor box", "other"),
    ])
    def test_guess(self, desc, expected):
        assert lldp.guess_platform(desc) == expected


class TestCapabilities:
    def test_letter_codes_expand(self):
        assert lldp.normalize_capabilities("B, R") == ["bridge", "router"]

    def test_full_words_passthrough(self):
        assert lldp.normalize_capabilities("bridge router") == ["bridge", "router"]

    def test_list_input(self):
        assert lldp.normalize_capabilities(["Bridge", "WLAN-AP"]) == ["bridge", "wlan-ap"]

    def test_empty(self):
        assert lldp.normalize_capabilities(None) == []
        assert lldp.normalize_capabilities("") == []

    def test_dict_input_truthy_keys(self):
        assert lldp.normalize_capabilities(
            {"bridge": True, "router": True, "wlan-access-point": False}
        ) == ["bridge", "router"]

    def test_fullname_variants_canonicalised(self):
        # AOS-CX / OpenConfig spellings fold onto the same tokens as letter codes.
        assert lldp.normalize_capabilities(
            ["wlan-access-point", "mac-bridge", "docsis-cable-device"]
        ) == ["wlan-ap", "bridge", "docsis"]

    def test_bare_word_variants_fold_to_canonical(self):
        # Real fleets (AOS-CX) advertise the bare word "wlan" for APs and "tel"
        # for phones — these must fold to the canonical tokens too.
        assert lldp.normalize_capabilities(["bridge", "wlan"]) == ["bridge", "wlan-ap"]
        assert lldp.normalize_capabilities(["bridge", "telephone"]) == ["bridge", "telephone"]
        assert lldp.normalize_capabilities(["tel"]) == ["telephone"]
        assert lldp.normalize_capabilities(["ap", "wifi"]) == ["wlan-ap"]
        assert lldp.normalize_capabilities(["switch"]) == ["bridge"]
        assert lldp.normalize_capabilities(["pc", "host"]) == ["station"]


class TestChassisType:
    def test_mac(self):
        assert lldp.infer_chassis_id_type("aa:bb:cc:dd:ee:ff") == "mac"

    def test_ip(self):
        assert lldp.infer_chassis_id_type("10.0.0.50") == "network-address"

    def test_other(self):
        assert lldp.infer_chassis_id_type("GigabitEthernet0/1") == ""


class TestPersistence:
    def test_neighbor_persisted_with_fields(self, devices, monkeypatch):
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [{
            "if_name": "Gi1",
            "lldp_neighbor_hostname": "unknown-switch-01",
            "lldp_neighbor_port": "GigabitEthernet0/1",
            "lldp_neighbor_desc": "uplink to core",
            "lldp_neighbor_mgmt_ip": "10.0.0.50",
            "lldp_neighbor_chassis_id": "aa:bb:cc:dd:ee:ff",
            "lldp_neighbor_system_desc": "Cisco IOS XE Software",
            "lldp_neighbor_capabilities": "B, R",
        }])
        topology.discover_links(a)
        n = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1")
        assert n.system_name == "unknown-switch-01"
        assert n.management_address == "10.0.0.50"
        assert n.chassis_id == "aa:bb:cc:dd:ee:ff"
        assert n.chassis_id_type == "mac"
        assert n.port_id == "GigabitEthernet0/1"
        assert n.port_description == "uplink to core"
        assert n.system_description == "Cisco IOS XE Software"
        assert n.capabilities == ["bridge", "router"]
        assert n.matched_device_id is None
        assert n.first_seen is not None and n.last_seen is not None

    def test_matched_neighbor_links_device(self, devices, monkeypatch):
        a, b = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi2", "lldp_neighbor_hostname": "rtr-02", "lldp_neighbor_port": "Gi2"}])
        topology.discover_links(a)
        n = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi2")
        assert n.matched_device_id == b.id

    def test_idempotent_one_row_per_port(self, devices, monkeypatch):
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi1", "lldp_neighbor_hostname": "mystery", "lldp_neighbor_port": "Gi1"}])
        topology.discover_links(a)
        first = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1").first_seen
        topology.discover_links(a)
        assert LLDPNeighbor.objects.filter(seen_by=a, local_interface="Gi1").count() == 1
        # first_seen is stable across re-scans.
        assert LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1").first_seen == first

    def test_stale_neighbor_pruned(self, devices, monkeypatch):
        # A port that stops advertising a neighbor (re-cabled / removed) drops its
        # stale LLDPNeighbor row on the next scan.
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi1", "lldp_neighbor_hostname": "ghost", "lldp_neighbor_port": "Gi1"},
            {"if_name": "Gi2", "lldp_neighbor_hostname": "keep", "lldp_neighbor_port": "Gi2"}])
        topology.discover_links(a)
        assert LLDPNeighbor.objects.filter(seen_by=a).count() == 2
        # Gi1's neighbor is gone next scan.
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi2", "lldp_neighbor_hostname": "keep", "lldp_neighbor_port": "Gi2"}])
        topology.discover_links(a)
        remaining = list(LLDPNeighbor.objects.filter(seen_by=a))
        assert len(remaining) == 1 and remaining[0].local_interface == "Gi2"

    def test_failed_scan_does_not_prune(self, devices, monkeypatch):
        # A collection failure (DiscoveryError) must NOT wipe existing rows.
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi1", "lldp_neighbor_hostname": "ghost", "lldp_neighbor_port": "Gi1"}])
        topology.discover_links(a)
        def boom(d):
            raise discovery.DiscoveryError("unreachable")
        monkeypatch.setattr(discovery, "discover_interfaces", boom)
        with pytest.raises(discovery.DiscoveryError):
            topology.discover_links(a)
        assert LLDPNeighbor.objects.filter(seen_by=a).count() == 1


class TestCollectAll:
    def test_collects_only_reachable_active_by_default(self, monkeypatch):
        live = Device.objects.create(hostname="live", ip_address="10.1.0.1",
                                     status="active", is_reachable=True)
        Device.objects.create(hostname="down", ip_address="10.1.0.2",
                              status="active", is_reachable=False)
        Device.objects.create(hostname="off", ip_address="10.1.0.3",
                              status="inactive", is_reachable=True)
        scanned = []

        def fake_discover(d):
            scanned.append(d.hostname)
            return [{"if_name": "Gi1", "lldp_neighbor_hostname": "x", "lldp_neighbor_port": "Gi1",
                     "matched_device_id": None}]

        monkeypatch.setattr(topology, "discover_links", fake_discover)
        summary = topology.collect_all_lldp()
        assert scanned == ["live"]
        assert summary == {"devices": 1, "neighbors": 1, "failed": 0}

    def test_one_failure_does_not_abort_sweep(self, monkeypatch):
        a = Device.objects.create(hostname="a", ip_address="10.2.0.1", status="active", is_reachable=True)
        b = Device.objects.create(hostname="b", ip_address="10.2.0.2", status="active", is_reachable=True)

        def fake_discover(d):
            if d.id == a.id:
                raise discovery.DiscoveryError("no creds")
            return [{"if_name": "Gi1", "matched_device_id": None}]

        monkeypatch.setattr(topology, "discover_links", fake_discover)
        summary = topology.collect_all_lldp()
        assert summary["devices"] == 2 and summary["failed"] == 1 and summary["neighbors"] == 1


class TestUndiscoveredEndpoint:
    def _seed(self, seen_by, **kw):
        defaults = dict(seen_by=seen_by, local_interface=kw.pop("local_interface", "Gi1"))
        defaults.update(kw)
        return LLDPNeighbor.objects.create(**defaults)

    def test_lists_only_unknown(self, devices, auth_client):
        a, b = devices
        # Unknown neighbor → listed.
        self._seed(a, local_interface="Gi1", system_name="ghost-sw", management_address="10.9.9.9",
                   system_description="Cisco IOS XE Software")
        # Matched neighbor (FK set) → excluded.
        self._seed(a, local_interface="Gi2", system_name="rtr-02", matched_device=b)
        # Neighbor whose mgmt IP is an existing device → excluded (live re-check).
        self._seed(a, local_interface="Gi3", system_name="x", management_address="10.0.0.2")
        # Neighbor whose system_name matches a device hostname → excluded.
        self._seed(a, local_interface="Gi4", system_name="rtr-02")

        resp = auth_client.get("/api/devices/lldp/undiscovered/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["system_name"] == "ghost-sw"
        assert row["in_inventory"] is False
        assert row["guessed_platform"] == "ios_xe"
        assert row["seen_by_device_hostname"] == "rtr-01"
        assert row["seen_on_interface"] == "Gi1"

    def test_count_endpoint(self, devices, auth_client):
        a, b = devices
        self._seed(a, local_interface="Gi1", system_name="ghost-1", management_address="10.9.9.1")
        self._seed(a, local_interface="Gi2", system_name="ghost-2", management_address="10.9.9.2")
        self._seed(a, local_interface="Gi3", system_name="rtr-02", matched_device=b)
        resp = auth_client.get("/api/devices/lldp/undiscovered/count/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 2}

    def test_requires_auth(self, devices, api_client):
        resp = api_client.get("/api/devices/lldp/undiscovered/count/")
        assert resp.status_code in (401, 403)


class TestUndiscoveredFilters:
    def _seed(self, seen_by, iface, **kw):
        return LLDPNeighbor.objects.create(
            seen_by=seen_by, local_interface=iface, **kw)

    @pytest.fixture
    def seeded(self, devices):
        a, _ = devices
        self._seed(a, "Gi1", system_name="core-sw", management_address="10.9.0.1",
                   capabilities=["bridge", "router"])
        self._seed(a, "Gi2", system_name="desk-phone", management_address="10.9.0.2",
                   capabilities=["telephone"])
        self._seed(a, "Gi3", system_name="alices-pc", management_address="10.9.0.3",
                   capabilities=["station"])
        self._seed(a, "Gi4", system_name="ap-lobby", capabilities=["wlan-ap", "bridge"])
        self._seed(a, "Gi5", system_name="mystery", capabilities=[])  # no caps
        return a

    def _names(self, resp):
        return sorted(r["system_name"] for r in resp.json()["results"])

    def test_default_excludes_unmanaged(self, seeded, auth_client):
        # Phones + workstations hidden by default; no-cap row always shown.
        resp = auth_client.get("/api/devices/lldp/undiscovered/")
        assert self._names(resp) == ["ap-lobby", "core-sw", "mystery"]

    def test_count_respects_default_exclusion(self, seeded, auth_client):
        resp = auth_client.get("/api/devices/lldp/undiscovered/count/")
        assert resp.json() == {"count": 3}

    def test_empty_exclude_shows_all(self, seeded, auth_client):
        resp = auth_client.get(
            "/api/devices/lldp/undiscovered/?exclude_capabilities=")
        assert self._names(resp) == [
            "alices-pc", "ap-lobby", "core-sw", "desk-phone", "mystery"]

    def test_include_capabilities(self, seeded, auth_client):
        # Only routers — plus the no-cap row, which is never dropped by caps.
        resp = auth_client.get(
            "/api/devices/lldp/undiscovered/"
            "?exclude_capabilities=&capabilities=router")
        assert self._names(resp) == ["core-sw", "mystery"]

    def test_has_ip_filter(self, seeded, auth_client):
        resp = auth_client.get(
            "/api/devices/lldp/undiscovered/?exclude_capabilities=&has_ip=false")
        assert self._names(resp) == ["ap-lobby", "mystery"]

    def test_search_filter(self, seeded, auth_client):
        resp = auth_client.get(
            "/api/devices/lldp/undiscovered/?exclude_capabilities=&search=phone")
        assert self._names(resp) == ["desk-phone"]

    def test_env_override_excluded_caps(self, seeded, auth_client, monkeypatch):
        monkeypatch.setenv("LLDP_EXCLUDE_CAPABILITIES", "wlan-ap")
        resp = auth_client.get("/api/devices/lldp/undiscovered/")
        # Now the AP is hidden but phones/PCs return.
        assert self._names(resp) == ["alices-pc", "core-sw", "desk-phone", "mystery"]

    def test_setting_overrides_env(self, seeded, auth_client, monkeypatch):
        from apps.core.models import SystemSetting

        monkeypatch.setenv("LLDP_EXCLUDE_CAPABILITIES", "wlan-ap")
        SystemSetting.set("lldp_exclude_capabilities", "telephone")  # persisted wins
        resp = auth_client.get("/api/devices/lldp/undiscovered/")
        assert self._names(resp) == ["alices-pc", "ap-lobby", "core-sw", "mystery"]


class TestLldpSettingsEndpoint:
    def test_get_defaults(self, auth_client):
        resp = auth_client.get("/api/settings/lldp/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["exclude_capabilities"] == ["telephone", "station", "docsis"]
        assert "wlan-ap" in body["available_capabilities"]
        assert body["default_exclude_capabilities"] == ["telephone", "station", "docsis"]

    def test_put_persists_and_canonicalises(self, auth_client):
        # full-name + letter variants fold to canonical tokens; dupes dropped.
        resp = auth_client.put(
            "/api/settings/lldp/",
            {"exclude_capabilities": ["wlan-access-point", "T", "telephone"]},
            format="json")
        assert resp.status_code == 200
        assert resp.json()["exclude_capabilities"] == ["wlan-ap", "telephone"]
        # GET reflects the persisted value.
        assert auth_client.get("/api/settings/lldp/").json()[
            "exclude_capabilities"] == ["wlan-ap", "telephone"]

    def test_put_empty_disables_exclusion(self, auth_client):
        resp = auth_client.put(
            "/api/settings/lldp/", {"exclude_capabilities": []}, format="json")
        assert resp.json()["exclude_capabilities"] == []

    def test_put_requires_admin(self, viewer_client):
        resp = viewer_client.put(
            "/api/settings/lldp/", {"exclude_capabilities": []}, format="json")
        assert resp.status_code in (401, 403)

    def test_put_rejects_non_list(self, auth_client):
        resp = auth_client.put(
            "/api/settings/lldp/", {"exclude_capabilities": "telephone"}, format="json")
        assert resp.status_code == 400
