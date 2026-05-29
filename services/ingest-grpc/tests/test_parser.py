"""
Unit tests for ingest.parser.

Uses lightweight mock objects that mirror the gNMI proto message interface,
so these tests run without compiled proto files.
"""
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from ingest.parser import notification_to_dict, path_to_str, typed_value_to_python


# ── Minimal proto mocks ───────────────────────────────────────────────────────

@dataclass
class _PathElem:
    name: str
    key: dict = field(default_factory=dict)


@dataclass
class _Path:
    elem: list = field(default_factory=list)
    origin: str = ""
    target: str = ""


class _TypedValue:
    """Mock a gNMI TypedValue oneof."""

    def __init__(self, kind: str, value: Any) -> None:
        self._kind = kind
        self._value = value
        setattr(self, kind, value)

    def WhichOneof(self, _field_name: str) -> str:
        return self._kind


@dataclass
class _Update:
    path: _Path = field(default_factory=_Path)
    val: Any = None
    duplicates: int = 0


@dataclass
class _Notification:
    timestamp: int = 0
    prefix: _Path = field(default_factory=_Path)
    update: list = field(default_factory=list)
    delete: list = field(default_factory=list)
    atomic: bool = False


# ── path_to_str ───────────────────────────────────────────────────────────────

class TestPathToStr:
    def test_empty_path_returns_slash(self):
        assert path_to_str(_Path()) == "/"

    def test_single_element(self):
        p = _Path(elem=[_PathElem("interfaces")])
        assert path_to_str(p) == "/interfaces"

    def test_nested_elements(self):
        p = _Path(elem=[_PathElem("interfaces"), _PathElem("interface"), _PathElem("state")])
        assert path_to_str(p) == "/interfaces/interface/state"

    def test_element_with_single_key(self):
        p = _Path(elem=[_PathElem("interface", {"name": "eth0"})])
        assert path_to_str(p) == "/interface[name=eth0]"

    def test_element_with_multiple_keys_sorted(self):
        p = _Path(elem=[_PathElem("peer", {"afi": "ipv4", "addr": "10.0.0.1"})])
        # keys must appear sorted for deterministic output
        assert path_to_str(p) == "/peer[addr=10.0.0.1,afi=ipv4]"

    def test_mixed_elements(self):
        p = _Path(
            elem=[
                _PathElem("interfaces"),
                _PathElem("interface", {"name": "Gi0/0"}),
                _PathElem("state"),
                _PathElem("oper-status"),
            ]
        )
        assert path_to_str(p) == "/interfaces/interface[name=Gi0/0]/state/oper-status"


# ── typed_value_to_python ─────────────────────────────────────────────────────

class TestTypedValueToPython:
    def test_string_val(self):
        assert typed_value_to_python(_TypedValue("string_val", "UP")) == "UP"

    def test_int_val(self):
        assert typed_value_to_python(_TypedValue("int_val", -42)) == -42

    def test_uint_val(self):
        assert typed_value_to_python(_TypedValue("uint_val", 100)) == 100

    def test_bool_val_true(self):
        assert typed_value_to_python(_TypedValue("bool_val", True)) is True

    def test_float_val(self):
        result = typed_value_to_python(_TypedValue("float_val", 3.14))
        assert abs(result - 3.14) < 1e-5

    def test_double_val(self):
        assert typed_value_to_python(_TypedValue("double_val", 1.23456789)) == pytest.approx(1.23456789)

    def test_ascii_val(self):
        assert typed_value_to_python(_TypedValue("ascii_val", "hello")) == "hello"

    def test_json_val_valid(self):
        payload = json.dumps({"oper-status": "UP", "speed": 1000}).encode()
        result = typed_value_to_python(_TypedValue("json_val", payload))
        assert result == {"oper-status": "UP", "speed": 1000}

    def test_json_ietf_val_valid(self):
        payload = json.dumps({"counters": {"in-pkts": 999}}).encode()
        result = typed_value_to_python(_TypedValue("json_ietf_val", payload))
        assert result["counters"]["in-pkts"] == 999

    def test_json_val_invalid_falls_back_to_string(self):
        result = typed_value_to_python(_TypedValue("json_val", b"not-json"))
        assert result == "not-json"

    def test_bytes_val_hex_encoded(self):
        result = typed_value_to_python(_TypedValue("bytes_val", b"\xde\xad\xbe\xef"))
        assert result == "deadbeef"

    def test_none_kind(self):
        tv = _TypedValue("string_val", "x")
        tv._kind = None  # type: ignore[assignment]
        assert typed_value_to_python(tv) is None

    def test_leaflist_val(self):
        elems = [_TypedValue("string_val", "a"), _TypedValue("int_val", 1)]

        class _ScalarArray:
            element = elems

        tv = _TypedValue("leaflist_val", _ScalarArray())
        tv.leaflist_val = _ScalarArray()
        assert typed_value_to_python(tv) == ["a", 1]


# ── notification_to_dict ──────────────────────────────────────────────────────

class TestNotificationToDict:
    def _make_update(self, path_str: str, value: Any, kind="string_val") -> _Update:
        elems = [_PathElem(seg) for seg in path_str.strip("/").split("/") if seg]
        return _Update(path=_Path(elem=elems), val=_TypedValue(kind, value))

    def test_basic_notification(self):
        notif = _Notification(
            timestamp=1_700_000_000_000_000_000,
            update=[self._make_update("/state/oper-status", "UP")],
        )
        result = notification_to_dict(notif, target="router1")
        assert result["timestamp_ns"] == 1_700_000_000_000_000_000
        assert result["target"] == "router1"
        assert len(result["updates"]) == 1
        assert result["updates"][0]["path"] == "/state/oper-status"
        assert result["updates"][0]["value"] == "UP"

    def test_prefix_overrides_caller_target(self):
        notif = _Notification(
            timestamp=0,
            prefix=_Path(target="device-from-proto"),
            update=[self._make_update("/metric", 42, "int_val")],
        )
        result = notification_to_dict(notif, target="caller-supplied")
        assert result["target"] == "device-from-proto"

    def test_prefix_path_prepended_to_update_path(self):
        prefix = _Path(elem=[_PathElem("interfaces"), _PathElem("interface", {"name": "eth0"})])
        notif = _Notification(
            timestamp=0,
            prefix=prefix,
            update=[self._make_update("/state/oper-status", "DOWN")],
        )
        result = notification_to_dict(notif, target="r1")
        expected_path = "/interfaces/interface[name=eth0]/state/oper-status"
        assert result["updates"][0]["path"] == expected_path

    def test_origin_captured(self):
        notif = _Notification(
            timestamp=0,
            prefix=_Path(origin="openconfig"),
            update=[self._make_update("/x", 1, "int_val")],
        )
        result = notification_to_dict(notif, target="r1")
        assert result["origin"] == "openconfig"

    def test_delete_paths(self):
        del_path = _Path(elem=[_PathElem("sessions"), _PathElem("session", {"id": "5"})])
        notif = _Notification(timestamp=0, delete=[del_path])
        result = notification_to_dict(notif, target="r1")
        assert result["deletes"] == ["/sessions/session[id=5]"]
        assert result["updates"] == []

    def test_multiple_updates(self):
        notif = _Notification(
            timestamp=0,
            update=[
                self._make_update("/a", "x"),
                self._make_update("/b", 99, "int_val"),
            ],
        )
        result = notification_to_dict(notif, target="r1")
        assert len(result["updates"]) == 2
        paths = [u["path"] for u in result["updates"]]
        assert "/a" in paths
        assert "/b" in paths

    def test_duplicates_field_preserved(self):
        upd = self._make_update("/counter", 5, "uint_val")
        upd.duplicates = 3
        notif = _Notification(timestamp=0, update=[upd])
        result = notification_to_dict(notif, target="r1")
        assert result["updates"][0]["duplicates"] == 3

    def test_json_value_in_update(self):
        payload = json.dumps({"in": 100, "out": 200}).encode()
        upd = _Update(
            path=_Path(elem=[_PathElem("counters")]),
            val=_TypedValue("json_ietf_val", payload),
        )
        notif = _Notification(timestamp=0, update=[upd])
        result = notification_to_dict(notif, target="r1")
        assert result["updates"][0]["value"] == {"in": 100, "out": 200}
