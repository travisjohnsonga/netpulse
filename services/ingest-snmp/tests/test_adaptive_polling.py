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


def test_essential_oids_polled_when_gnmi_active():
    # When gNMI is active the full profile is replaced by the essential system
    # OIDs (sysUpTime/Descr/Name/Location) — uptime keeps flowing at minimal load.
    from ingest.poller import ALWAYS_POLL_OIDS
    pub = _FakePublisher()
    poller = _poller(_FakeActivity(True), pub)
    dev = _device_metrics()  # profile has only a gNMI-covered CPU OID

    seen = {}

    async def _ok_snmp(device, oids, creds, engine):
        seen["oids"] = oids
        return {"1.3.6.1.2.1.1.3.0": {"value": "123", "type": "TimeTicks", "name": "sysUpTime.0"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    # The CPU OID is dropped; the 4 essential OIDs (incl. sysUpTime) are polled.
    assert seen["oids"] == list(ALWAYS_POLL_OIDS.values())
    assert "1.3.6.1.2.1.1.3.0" in seen["oids"]
    assert len(pub.published) == 1
    assert poller._suppressed["8"] is True


def test_poll_runs_when_gnmi_inactive():
    pub = _FakePublisher()
    poller = _poller(_FakeActivity(False), pub)
    dev = _device()

    async def _ok_snmp(device, oids, creds, engine):
        return {"1.3.6.1.2.1.1.3.0": {"value": "123", "type": "TimeTicks", "name": "sysUpTime.0"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    assert len(pub.published) == 1
    device_id, payload = pub.published[0]
    assert device_id == "7"
    assert payload["protocol"] == "snmp"
    assert poller._suppressed["7"] is False


def test_fresh_engine_per_poll_is_closed():
    # Each poll builds a new SnmpEngine and closes its dispatcher afterwards,
    # so stale SNMPv3 engineBoots/engineTime can't wedge subsequent polls.
    pub = _FakePublisher()
    poller = _poller(None, pub)
    dev = _device()
    engines = []

    class _FakeEngine:
        def __init__(self):
            self.closed = False

        def closeDispatcher(self):
            self.closed = True

    def _fake_new_engine():
        e = _FakeEngine()
        engines.append(e)
        return e

    async def _ok_snmp(device, oids, creds, engine):
        assert engine is engines[-1]   # the fresh engine is threaded through
        return {"1.3.6.1.2.1.1.3.0": {"value": "1", "type": "TimeTicks", "name": "sysUpTime.0"}}

    poller._new_engine = _fake_new_engine
    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))

    assert len(engines) == 2                  # a fresh engine each poll
    assert all(e.closed for e in engines)     # all closed


def test_no_activity_checker_always_polls():
    # gnmi_activity=None disables adaptive polling entirely.
    pub = _FakePublisher()
    poller = _poller(None, pub)
    dev = _device()

    async def _ok_snmp(device, oids, creds, engine):
        return {"x": {"value": "1", "type": "Integer", "name": "x"}}

    poller._snmp_get = _ok_snmp
    asyncio.run(poller._poll(dev, dev.poll_profiles[0]))
    assert len(pub.published) == 1
