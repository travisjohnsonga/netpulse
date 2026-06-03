"""Regression tests for placeholder-secret protection.

Root cause guarded here: integration/unit-test fixture secrets (e.g.
"sup3r-secret-pw", "authkey123") historically leaked into a live OpenBao at
``netpulse/credentials/{pk}``. Because the vault path reuses the profile pk,
those survived a Postgres reset and were read back by a newly-created profile —
making real credentials appear to "revert" to placeholder values.

The defenses, all exercised below:
  * vault.write_secret refuses to persist a placeholder to a real vault.
  * vault.read_secret scrubs any placeholder still sitting at a path.
  * the credential serializer rejects placeholder secrets with a clean 400.
"""
import pytest

from apps.credentials import vault
from apps.credentials.models import CredentialProfile

pytestmark = pytest.mark.django_db


class _FakeKV:
    """Minimal stand-in for hvac's kv.v2 used by vault._client()."""

    def __init__(self, store):
        self._store = store

    def create_or_update_secret(self, path, secret, mount_point):
        self._store[path] = dict(secret)

    def read_secret_version(self, path, mount_point, raise_on_deleted_version):
        return {"data": {"data": dict(self._store[path])}}


class _FakeClient:
    def __init__(self, store):
        self.secrets = type("S", (), {"kv": type("KV", (), {"v2": _FakeKV(store)})()})()


@pytest.fixture
def live_vault(monkeypatch):
    """Pretend OpenBao is configured, backed by an in-memory store."""
    store = {}
    monkeypatch.setattr(vault, "vault_enabled", lambda: True)
    monkeypatch.setattr(vault, "_client", lambda: _FakeClient(store))
    return store


def test_is_placeholder():
    assert vault.is_placeholder("sup3r-secret-pw")
    assert vault.is_placeholder("authkey123")
    assert vault.is_placeholder("privkey123")
    assert vault.is_placeholder("password")
    assert not vault.is_placeholder("RealAuthKey-8chr")
    assert not vault.is_placeholder("")
    assert not vault.is_placeholder(None)


def test_write_secret_refuses_placeholder(live_vault):
    with pytest.raises(ValueError) as exc:
        vault.write_secret("netpulse/credentials/1", {"ssh_password": "sup3r-secret-pw"})
    assert "placeholder" in str(exc.value).lower()
    assert "netpulse/credentials/1" not in live_vault  # nothing written


def test_write_secret_allows_real_value(live_vault):
    vault.write_secret("netpulse/credentials/1", {"ssh_password": "RealPw-9f3a"})
    assert live_vault["netpulse/credentials/1"] == {"ssh_password": "RealPw-9f3a"}


def test_read_secret_scrubs_placeholders(live_vault):
    # Simulate a stale leak sitting at the path next to a real value.
    live_vault["netpulse/credentials/1"] = {
        "ssh_password": "RealPw-9f3a",
        "snmpv3_priv_key": "privkey123",
    }
    got = vault.read_secret("netpulse/credentials/1")
    assert got == {"ssh_password": "RealPw-9f3a"}  # placeholder dropped


def test_write_secret_refuses_test_fixtures(live_vault):
    # The real-looking integration fixtures must never persist either, even
    # though they are intentionally NOT placeholders for the public contract
    # (is_placeholder stays False for them — see test_is_placeholder).
    for val in ("Sup3rRealPw-2f9a", "RealAuthKey-8chr", "RealPrivKey-8chr"):
        with pytest.raises(ValueError):
            vault.write_secret("netpulse/credentials/1", {"ssh_password": val})
    assert "netpulse/credentials/1" not in live_vault  # nothing written


def test_read_secret_scrubs_test_fixtures(live_vault):
    # A stale fixture leak (from a pre-isolation run) must be dropped on read so
    # a real profile reusing the pk never inherits it.
    live_vault["netpulse/credentials/9"] = {
        "ssh_password": "RealPw-9f3a",
        "snmpv3_auth_key": "RealAuthKey-8chr",
        "snmpv3_priv_key": "RealPrivKey-8chr",
    }
    got = vault.read_secret("netpulse/credentials/9")
    assert got == {"ssh_password": "RealPw-9f3a"}  # fixtures dropped


def test_serializer_rejects_placeholder_secret(auth_client):
    resp = auth_client.post("/api/credentials/", {
        "name": "BadPlaceholder", "ssh_enabled": True, "ssh_username": "u",
        "ssh_auth_method": "password", "ssh_password": "password",
    }, format="json")
    assert resp.status_code == 400
    assert "ssh_password" in resp.json()


def test_reset_test_data_cleans_vault(live_vault):
    """Soft factory reset must delete the OpenBao secret, not orphan it."""
    from django.core.management import call_command

    # delete_secret needs a delete_metadata path on the fake; track deletes.
    deleted = []
    live_vault["netpulse/credentials/seed"] = {"ssh_password": "RealPw-9f3a"}

    class _DelKV(_FakeKV):
        def delete_metadata_and_all_versions(self, path, mount_point):
            deleted.append(path)
            self._store.pop(path, None)

    store = live_vault
    kv = _DelKV(store)
    client = type("C", (), {})()
    client.secrets = type("S", (), {"kv": type("KV", (), {"v2": kv})()})()
    import unittest.mock as mock
    with mock.patch.object(vault, "_client", lambda: client):
        CredentialProfile.objects.create(
            name="seed-profile", ssh_enabled=True, ssh_username="u",
            vault_path="netpulse/credentials/seed",
        )
        call_command("reset_test_data")
    assert "netpulse/credentials/seed" in deleted
