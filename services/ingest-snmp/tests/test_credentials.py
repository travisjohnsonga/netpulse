"""
Tests for the OpenBao CredentialManager — focused on the restart/reboot bug:
the OpenBao token must be resolved *lazily* (re-read on demand), not frozen at
process start, so the poller self-heals once .init_keys is readable and OpenBao
is unsealed.
"""
import asyncio
import sys
import types

import pytest

from ingest.credentials import CredentialError, CredentialManager


class _FakeKV:
    """Fake hvac KV-v2 endpoint: 403s on an empty/"stale" token, else returns data."""

    def __init__(self, client):
        self._c = client

    def read_secret_version(self, path, mount_point):
        if not self._c.token or self._c.token == "stale":
            raise RuntimeError("permission denied (403)")
        return {"data": {"data": {"username": "admin", "snmpv3_auth_key": f"K-{path}"}}}


class _FakeClient:
    def __init__(self, url=None, token=None):
        self.url = url
        self.token = token
        self.secrets = types.SimpleNamespace(
            kv=types.SimpleNamespace(v2=_FakeKV(self))
        )


@pytest.fixture
def fake_hvac(monkeypatch):
    mod = types.ModuleType("hvac")
    mod.Client = _FakeClient
    monkeypatch.setitem(sys.modules, "hvac", mod)
    return mod


def test_static_token_fetch_ok(fake_hvac):
    cm = CredentialManager("http://bao:8200", token="s.root")
    out = asyncio.run(cm.get("snmp/1"))
    assert out == {"username": "admin", "snmpv3_auth_key": "K-snmp/1"}


def test_caches_within_ttl(fake_hvac, monkeypatch):
    cm = CredentialManager("http://bao:8200", token="s.root", cache_ttl=300)
    calls = {"n": 0}
    real = cm._fetch
    monkeypatch.setattr(cm, "_fetch", lambda p: (calls.__setitem__("n", calls["n"] + 1), real(p))[1])
    asyncio.run(cm.get("snmp/1"))
    asyncio.run(cm.get("snmp/1"))
    assert calls["n"] == 1  # second read served from cache


def test_client_rebuilt_when_token_changes(fake_hvac):
    """The crux of the fix: an empty/old token is replaced when it changes."""
    state = {"val": "s.old"}
    cm = CredentialManager("http://bao:8200", token_provider=lambda: state["val"])
    c1 = cm._get_client()
    assert c1.token == "s.old"
    assert cm._get_client() is c1            # same token → same client
    state["val"] = "s.new"
    c2 = cm._get_client()
    assert c2 is not c1 and c2.token == "s.new"  # rotated → rebuilt


def test_self_heals_when_token_arrives_after_start(fake_hvac):
    """
    Reproduces the reboot race: token empty when the manager is built (OpenBao
    not yet unsealed / .init_keys not yet readable), then becomes available.
    The SAME manager must succeed afterwards — no container recreate needed.
    """
    state = {"val": ""}  # simulate .init_keys not yet readable
    cm = CredentialManager("http://bao:8200", token_provider=lambda: state["val"])

    with pytest.raises(CredentialError):       # empty token → fetch fails, surfaced
        asyncio.run(cm.get("snmp/1"))

    state["val"] = "s.root"                      # keys file now present / unsealed
    out = asyncio.run(cm.get("snmp/1"))          # self-heals on next poll cycle
    assert out["username"] == "admin"


def test_retry_once_on_transient_failure(fake_hvac, monkeypatch):
    """A transient first failure (e.g. sealed→unsealed, same token) is retried."""
    cm = CredentialManager("http://bao:8200", token="s.root")
    calls = {"n": 0}
    real = cm._fetch

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("core is sealed")
        return real(path)

    monkeypatch.setattr(cm, "_fetch", flaky)
    out = asyncio.run(cm.get("snmp/1"))
    assert calls["n"] == 2                        # failed once, retried, succeeded
    assert out["username"] == "admin"
