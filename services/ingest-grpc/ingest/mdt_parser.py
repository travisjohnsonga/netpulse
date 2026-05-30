"""
Parse Cisco MDT telemetry.Telemetry (encode-kvgpb) messages into plain dicts.

With key-value GPB, each `data_gpbkv` entry is a tree of TelemetryField. For
IOS-XE YANG paths each top-level entry is one instance (one interface, one
component, …) with a "keys" subtree (the list keys, e.g. interface name) and a
"content" subtree (the leaf values). We flatten those into {keys}/{content}
dicts and also produce a flat numeric `metrics` map for InfluxDB.
"""
from __future__ import annotations

from typing import Any


def field_value(tf) -> Any:
    """Unwrap a TelemetryField value_by_type oneof to a Python value (or None)."""
    kind = tf.WhichOneof("value_by_type")
    if kind is None:
        return None
    val = getattr(tf, kind)
    if kind == "bytes_value":
        return val.hex()
    return val


def _flatten(fields, prefix: str = "") -> dict:
    """Recursively flatten a list of TelemetryField into {path: value} leaves."""
    out: dict = {}
    for f in fields:
        name = f.name or ""
        key = name if not prefix else (f"{prefix}/{name}" if name else prefix)
        val = field_value(f)
        if val is not None and not f.fields:
            out[key] = val
        if f.fields:
            out.update(_flatten(f.fields, key))
    return out


def _row_keys_content(entry) -> tuple[dict, dict]:
    """Split one data_gpbkv entry into (keys, content) dicts."""
    keys, content = {}, {}
    for sub in entry.fields:
        if sub.name == "keys":
            keys = _flatten(sub.fields)
        elif sub.name == "content":
            content = _flatten(sub.fields)
    # Some encodings put leaves directly under the entry (no keys/content split).
    if not keys and not content:
        content = _flatten(entry.fields)
    return keys, content


def parse_telemetry(telemetry) -> dict:
    """Convert a telemetry.Telemetry proto into a JSON-serialisable dict."""
    rows = []
    for entry in telemetry.data_gpbkv:
        keys, content = _row_keys_content(entry)
        if keys or content:
            rows.append({"keys": keys, "content": content})
    return {
        "node_id": telemetry.node_id_str,
        "subscription": telemetry.subscription_id_str,
        "encoding_path": telemetry.encoding_path,
        "collection_id": telemetry.collection_id,
        "msg_timestamp": telemetry.msg_timestamp,
        "rows": rows,
    }


def _label(keys: dict) -> str:
    """A short, NATS/InfluxDB-safe label for a row from its keys (e.g. if name)."""
    if not keys:
        return ""
    # Prefer a "name" key (interfaces, components); else join all key values.
    for cand in ("name", "interface-name", "node-name"):
        if cand in keys:
            return str(keys[cand])
    return "_".join(str(v) for v in keys.values())


def flatten_metrics(parsed: dict) -> dict:
    """
    Build a flat numeric metrics map keyed by "<row-label>/<leaf>" → value, in
    the nested form the stream-processor's _extract_fields understands:
        {field_name: {"name": ..., "value": <num>, "type": "gnmi"}}
    Only numeric leaves are kept.
    """
    metrics: dict = {}
    for row in parsed.get("rows", []):
        label = _label(row.get("keys", {}))
        for leaf, val in row.get("content", {}).items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            # Keep only the final leaf token to stay readable (e.g. "in-octets").
            leaf_name = leaf.rsplit("/", 1)[-1]
            name = f"{label}/{leaf_name}" if label else leaf_name
            metrics[name] = {"name": name, "value": val, "type": "gnmi"}
    return metrics
