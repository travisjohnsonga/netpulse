"""
Unit tests for ingest.mdt_parser using lightweight mocks of the Cisco
telemetry.Telemetry / TelemetryField proto interface (no compiled protos).
"""
from ingest.mdt_parser import field_value, flatten_metrics, parse_telemetry


class TF:
    """Mock TelemetryField: name, nested fields, and one typed value."""

    def __init__(self, name="", fields=None, kind=None, value=None):
        self.name = name
        self.fields = fields or []
        self._kind = kind
        if kind is not None:
            setattr(self, kind, value)

    def WhichOneof(self, which):
        return self._kind


class Telem:
    def __init__(self, node="", sub="", path="", ts=0, cid=0, rows=None):
        self.node_id_str = node
        self.subscription_id_str = sub
        self.encoding_path = path
        self.msg_timestamp = ts
        self.collection_id = cid
        self.data_gpbkv = rows or []


def _iface_row(name, in_oct, out_oct, in_err):
    return TF(fields=[
        TF(name="keys", fields=[TF(name="name", kind="string_value", value=name)]),
        TF(name="content", fields=[
            TF(name="in-octets", kind="uint64_value", value=in_oct),
            TF(name="out-octets", kind="uint64_value", value=out_oct),
            TF(name="statistics", fields=[
                TF(name="in-errors", kind="uint64_value", value=in_err),
            ]),
            TF(name="description", kind="string_value", value="uplink"),
        ]),
    ])


def _telem():
    return Telem(
        node="router1", sub="101",
        path="interfaces-ios-xe-oper:interfaces/interface",
        ts=1700000000000,
        rows=[_iface_row("GigabitEthernet1", 1000, 2000, 0),
              _iface_row("GigabitEthernet2", 50, 60, 3)],
    )


class TestFieldValue:
    def test_typed_values(self):
        assert field_value(TF(kind="uint64_value", value=42)) == 42
        assert field_value(TF(kind="string_value", value="x")) == "x"
        assert field_value(TF(kind="bytes_value", value=b"\x01\x02")) == "0102"
        assert field_value(TF()) is None  # no value set


class TestParseTelemetry:
    def test_metadata_and_rows(self):
        p = parse_telemetry(_telem())
        assert p["node_id"] == "router1" and p["subscription"] == "101"
        assert p["encoding_path"].endswith("interfaces/interface")
        assert len(p["rows"]) == 2
        r0 = p["rows"][0]
        assert r0["keys"] == {"name": "GigabitEthernet1"}
        assert r0["content"]["in-octets"] == 1000
        assert r0["content"]["statistics/in-errors"] == 0   # nested flattened
        assert r0["content"]["description"] == "uplink"


class TestFlattenMetrics:
    def test_numeric_only_keyed_by_interface(self):
        m = flatten_metrics(parse_telemetry(_telem()))
        # numeric leaves only; string "description" excluded
        assert m["GigabitEthernet1/in-octets"]["value"] == 1000
        assert m["GigabitEthernet1/out-octets"]["value"] == 2000
        assert m["GigabitEthernet1/in-errors"]["value"] == 0       # last leaf token
        assert m["GigabitEthernet2/in-errors"]["value"] == 3
        assert all(v["type"] == "gnmi" for v in m.values())
        assert not any("description" in k for k in m)
