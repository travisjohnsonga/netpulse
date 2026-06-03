"""Tests for ingest.models — no external dependencies required."""
import pytest
from ingest.models import Device, PollProfile


class TestPollProfile:
    def test_from_dict_basic(self):
        p = PollProfile.from_dict({"name": "if", "oids": ["1.3.6.1.2.1.2.2.1.10"]})
        assert p.name == "if"
        assert p.oids == ["1.3.6.1.2.1.2.2.1.10"]
        assert p.interval_seconds == 60

    def test_from_dict_custom_interval(self):
        p = PollProfile.from_dict({"name": "bgp", "oids": [], "interval_seconds": 30})
        assert p.interval_seconds == 30


class TestDevice:
    _MINIMAL = {"device_id": "r1", "ip": "10.0.0.1"}

    def test_minimal_device(self):
        d = Device.from_dict(self._MINIMAL)
        assert d.device_id == "r1"
        assert d.ip == "10.0.0.1"
        assert d.port == 161
        assert d.version == 2
        assert d.cred_path == ""
        assert d.poll_profiles == []
        assert d.walk_oids == []

    def test_walk_oids_round_trip(self):
        bases = ["1.3.6.1.2.1.25.3.3.1.2", "1.3.6.1.2.1.99.1.1.1.4"]
        d = Device.from_dict({**self._MINIMAL, "walk_oids": bases})
        assert d.walk_oids == bases
        assert d.to_dict()["walk_oids"] == bases

    def test_hostname_defaults_to_ip(self):
        d = Device.from_dict(self._MINIMAL)
        assert d.hostname == "10.0.0.1"

    def test_explicit_hostname(self):
        d = Device.from_dict({**self._MINIMAL, "hostname": "router1.example.com"})
        assert d.hostname == "router1.example.com"

    def test_label_uses_hostname(self):
        d = Device.from_dict({**self._MINIMAL, "hostname": "rtr1"})
        assert d.label == "rtr1"

    def test_label_falls_back_to_ip(self):
        d = Device.from_dict(self._MINIMAL)
        assert d.label == "10.0.0.1"

    def test_version_3(self):
        d = Device.from_dict({**self._MINIMAL, "version": 3, "cred_path": "snmp/r1"})
        assert d.version == 3
        assert d.cred_path == "snmp/r1"

    def test_poll_profiles_parsed(self):
        d = Device.from_dict({
            **self._MINIMAL,
            "poll_profiles": [
                {"name": "sys", "oids": ["1.3.6.1.2.1.1.3"], "interval_seconds": 30},
                {"name": "if",  "oids": ["1.3.6.1.2.1.2.2.1.10"], "interval_seconds": 60},
            ],
        })
        assert len(d.poll_profiles) == 2
        assert d.poll_profiles[0].name == "sys"
        assert d.poll_profiles[1].interval_seconds == 60

    def test_shorthand_poll_oids(self):
        d = Device.from_dict({
            **self._MINIMAL,
            "poll_oids": ["1.3.6.1.2.1.1.3.0"],
            "poll_interval": 120,
        })
        assert len(d.poll_profiles) == 1
        assert d.poll_profiles[0].name == "default"
        assert d.poll_profiles[0].interval_seconds == 120

    def test_poll_profiles_take_precedence_over_shorthand(self):
        # Explicit poll_profiles should win; poll_oids ignored when profiles present
        d = Device.from_dict({
            **self._MINIMAL,
            "poll_profiles": [{"name": "x", "oids": ["1.3"]}],
            "poll_oids": ["9.9"],
        })
        assert len(d.poll_profiles) == 1
        assert d.poll_profiles[0].name == "x"

    def test_missing_device_id_raises(self):
        with pytest.raises(KeyError):
            Device.from_dict({"ip": "1.1.1.1"})

    def test_missing_ip_raises(self):
        with pytest.raises(KeyError):
            Device.from_dict({"device_id": "x"})

    def test_roundtrip(self):
        original = Device.from_dict({
            **self._MINIMAL,
            "hostname": "r1.example.com",
            "port": 1161,
            "version": 3,
            "cred_path": "snmp/r1",
            "poll_profiles": [{"name": "a", "oids": ["1.2.3"], "interval_seconds": 45}],
        })
        reconstructed = Device.from_dict(original.to_dict())
        assert reconstructed.device_id == original.device_id
        assert reconstructed.hostname == original.hostname
        assert reconstructed.version == original.version
        assert reconstructed.poll_profiles[0].interval_seconds == 45
