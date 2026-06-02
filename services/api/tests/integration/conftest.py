"""
Integration-suite conftest.

The top-level tests/conftest.py provides the shared fixtures (api_client,
auth_client, user, per-role clients). Because tests/integration/ is a
subdirectory, those fixtures apply here automatically — we only add the
`requires_devices` gate.

Tests marked `requires_devices` exercise real network-device I/O (SSH/SNMP/gNMI
to live gear) and are skipped by default so the suite is green without devices.
Set NETPULSE_DEVICE_TESTS=1 to run them.
"""
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_devices: test needs real network device I/O; "
        "skipped unless NETPULSE_DEVICE_TESTS=1",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("NETPULSE_DEVICE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="requires real devices; set NETPULSE_DEVICE_TESTS=1")
    for item in items:
        if "requires_devices" in item.keywords:
            item.add_marker(skip)
