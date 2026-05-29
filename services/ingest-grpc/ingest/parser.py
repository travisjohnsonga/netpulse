"""
Parse gNMI Notification proto messages into plain Python dicts
suitable for JSON serialisation and NATS publishing.
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def path_to_str(path) -> str:
    """
    Convert a gNMI Path proto to a YANG-style string.

    Example output: /interfaces/interface[name=eth0]/state/oper-status
    """
    parts = []
    for elem in path.elem:
        part = elem.name
        if elem.key:
            keys = ",".join(f"{k}={v}" for k, v in sorted(elem.key.items()))
            part = f"{part}[{keys}]"
        parts.append(part)
    return ("/" + "/".join(parts)) if parts else "/"


def typed_value_to_python(tv) -> Any:
    """
    Unwrap a gNMI TypedValue oneof into a Python-native value.

    JSON / JSON-IETF bytes are decoded to dicts/lists.
    Raw bytes are hex-encoded. Leaf-lists become Python lists.
    Returns None for unrecognised or unset variants.
    """
    kind = tv.WhichOneof("value")
    if kind is None:
        return None

    if kind in ("string_val", "ascii_val"):
        return getattr(tv, kind)
    if kind in ("int_val", "uint_val", "bool_val", "float_val", "double_val"):
        return getattr(tv, kind)
    if kind in ("json_val", "json_ietf_val"):
        raw: bytes = getattr(tv, kind)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw.decode("utf-8", errors="replace")
    if kind == "bytes_val":
        return tv.bytes_val.hex()
    if kind == "leaflist_val":
        return [typed_value_to_python(elem) for elem in tv.leaflist_val.element]
    if kind == "proto_bytes":
        return tv.proto_bytes.hex()

    logger.debug("unhandled TypedValue kind: %s", kind)
    return None


def notification_to_dict(notification, target: str) -> dict:
    """
    Convert a gNMI Notification proto to a JSON-serialisable dict.

    Schema:
    {
        "timestamp_ns": <int>,       # nanoseconds since Unix epoch
        "target":       <str>,       # device identifier (from prefix or caller)
        "origin":       <str>,       # YANG origin (e.g. "openconfig", "native")
        "prefix":       <str>,       # prefix path as YANG string
        "updates": [
            {"path": <str>, "value": <any>, "duplicates": <int>},
            ...
        ],
        "deletes": [<str>, ...],     # deleted paths
    }
    """
    prefix_str = ""
    origin = ""

    # prefix is a message field; access target/origin directly (default "")
    if notification.prefix.target:
        target = notification.prefix.target
    if notification.prefix.origin:
        origin = notification.prefix.origin
    if notification.prefix.elem:
        prefix_str = path_to_str(notification.prefix)

    updates = []
    for upd in notification.update:
        path_str = prefix_str + path_to_str(upd.path)
        updates.append(
            {
                "path": path_str,
                "value": typed_value_to_python(upd.val),
                "duplicates": upd.duplicates,
            }
        )

    deletes = [prefix_str + path_to_str(d) for d in notification.delete]

    return {
        "timestamp_ns": notification.timestamp,
        "target": target,
        "origin": origin,
        "prefix": prefix_str,
        "updates": updates,
        "deletes": deletes,
    }
