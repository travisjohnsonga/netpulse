"""
The SNMPPoller suppresses a device's SNMP poll while gNMI is active, and polls
normally otherwise. Drives the async _poll directly with stubbed collaborators.
"""
import asyncio

from ingest.models import Device, PollProfile
from ingest.poller import SNMPPoller


class _FakeActivity:
    def __init__(self, active):
        self.active = active

    async def is_active(self, device_id):
        return self.active


class _FakePublisher:
    def __init__(self):
        self.published = []

    async def publish_metrics(self, device_id, payload):
        self.published.append((device_id, payload))


def _device():
    return Device.from_dict({
        "device_id": "7", "hostname": "r7", "ip": "10.0.0.7", "version": 2,
        "poll_profiles": [{"name": "device", "oids": ["1.3.6.1.2.1.1.3.0"]}],
    })


def _poller(activity, publisher):
    p = SNMPPoller(credentials=None, publisher=publisher, gnmi_activity=activity)
    return p


def _device_metrics():
    # A profile of device-metric OIDs that gNMI covers (none are always-poll).
    return Device.from_dict({
        "device_id": "8", "hostname": "r8", "ip": "10.0.0.8", "version": 2,
        "poll_profiles": [{"name": "device", "oids": ["1.3.6.1.4.1.9.9.109.1.1.1.1.8.1"]}],
    })


def test_poll_skipped_when_gnmi_active_for_covered_oids():
    # A profile with only gNMI-covered metric OIDs is fully suppressed.
    pub = _FakePublisher()
    poller = _poller(_FakeActivity(True), pub)
    dev = _device_metrics()

    snmp_called = False

    async def _fail_snmp(*a, **k):
        nonlocal snmp_called
        snmp_called = True
        return {}

    poller._snmp_get = _fail_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    assert snmp_called is False
    assert pub.published == []
    assert poller._suppressed["8"] is True


def test_sysuptime_still_polled_when_gnmi_active():
    # gNMI doesn't carry uptime — sysUpTime must still be polled (fix #3), so the
    # profile is reduced to the always-poll OIDs rather than skipped entirely.
    pub = _FakePublisher()
    poller = _poller(_FakeActivity(True), pub)
    dev = _device()  # profile oids = [sysUpTime]

    seen_oids = {}

    async def _ok_snmp(device, oids, creds):
        seen_oids["oids"] = oids
        return {"1.3.6.1.2.1.1.3.0": {"value": "123", "type": "TimeTicks", "name": "sysUpTime.0"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    assert seen_oids["oids"] == ["1.3.6.1.2.1.1.3.0"]  # only the always-poll OID
    assert len(pub.published) == 1
    assert poller._suppressed["7"] is True  # device metrics still marked suppressed


def test_poll_runs_when_gnmi_inactive():
    pub = _FakePublisher()
    poller = _poller(_FakeActivity(False), pub)
    dev = _device()

    async def _ok_snmp(device, oids, creds):
        return {"1.3.6.1.2.1.1.3.0": {"value": "123", "type": "TimeTicks", "name": "sysUpTime.0"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    assert len(pub.published) == 1
    device_id, payload = pub.published[0]
    assert device_id == "7"
    assert payload["protocol"] == "snmp"
    assert poller._suppressed["7"] is False


def test_no_activity_checker_always_polls():
    # gnmi_activity=None disables adaptive polling entirely.
    pub = _FakePublisher()
    poller = _poller(None, pub)
    dev = _device()

    async def _ok_snmp(device, oids, creds):
        return {"x": {"value": "1", "type": "Integer", "name": "x"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))
    assert len(pub.published) == 1
