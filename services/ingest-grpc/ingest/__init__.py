"""
ingest — gNMI/gRPC dial-out streaming telemetry receiver.

Adds proto_generated/ to sys.path so compiled protobuf modules
(gnmi_pb2, gnmi_pb2_grpc, …) are importable by their short names,
matching the import style that grpc_tools.protoc generates.
"""
import os
import sys

_PROTO_GENERATED = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "proto_generated")
)
if os.path.isdir(_PROTO_GENERATED) and _PROTO_GENERATED not in sys.path:
    sys.path.insert(0, _PROTO_GENERATED)
