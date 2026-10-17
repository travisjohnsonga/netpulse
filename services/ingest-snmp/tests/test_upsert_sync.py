"""Poller upsert is idempotent: the API's periodic full resync must not
restart unchanged devices (resetting poll cadence), while changes, dead loops
and Poll Now (force) do restart."""
import asyncio

from ingest.models import Device
from ingest.poller import SNMPPoller


def _device(**kw):
    d = {"device_id": "7", "hostname": "sw1", "ip": "192.0.2.7", "version": 3,
         "cred_path": "snmp/x", "snmp_username": "u",
         "poll_oids": ["1.3.6.1.2.1.1.3.0"], "poll_interval": 300}
    d.update(kw)
    return Device.from_dict(d)


def _run(scenario):
    starts = []

    async def main():
        poller = SNMPPoller(credentials=None, publisher=None)

        async def fake_loop(device, profile):
            starts.append(device.ip)
            await asyncio.sleep(3600)

        poller._poll_loop = fake_loop
        await scenario(poller)
        await asyncio.sleep(0)            # let started tasks run their first step
        poller.stop_all()
        return poller

    asyncio.run(main())
    return starts


def test_unchanged_upsert_is_ignored():
    async def s(p):
        assert p.upsert(_device()) is True
        await asyncio.sleep(0)
        assert p.upsert(_device()) is False         # periodic resync, same config
    assert _run(s) == ["192.0.2.7"]


def test_changed_config_restarts():
    async def s(p):
        p.upsert(_device())
        await asyncio.sleep(0)
        assert p.upsert(_device(ip="192.0.2.8")) is True
        assert p.upsert(_device(ip="192.0.2.8", poll_oids=["1.3.6.1.2.1.1.5.0"])) is True
    assert len(_run(s)) >= 2


def test_force_restarts_unchanged():
    async def s(p):
        p.upsert(_device())
        await asyncio.sleep(0)
        assert p.upsert(_device(), force=True) is True   # Poll Now
    assert _run(s) == ["192.0.2.7", "192.0.2.7"]


def test_dead_loop_is_restarted():
    async def s(p):
        p.upsert(_device())
        await asyncio.sleep(0)
        for t in p._tasks["7"]:
            t.cancel()
        await asyncio.sleep(0)
        assert p.upsert(_device()) is True
    assert len(_run(s)) == 2


def test_remove_forgets_config():
    async def s(p):
        p.upsert(_device())
        await asyncio.sleep(0)
        p.remove("7")
        assert "7" not in p._devices
        assert p.upsert(_device()) is True            # re-added after removal
    assert len(_run(s)) == 2


def test_no_profile_device_unchanged_is_quiet():
    async def s(p):
        assert p.upsert(_device(poll_oids=[])) is True
        assert p.upsert(_device(poll_oids=[])) is False
    assert _run(s) == []
