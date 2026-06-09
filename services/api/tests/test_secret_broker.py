"""Secret-broker authorization — negative cases are the priority.

Identity is the authenticated transport account; the allowed set is derived
server-side via resolve.effective_collector; the request can only narrow. Every
branch must deny safely and audit.
"""
import logging

import pytest

from apps.collectors import secret_broker as sb
from apps.collectors.models import Collector

# The real scoped reader, captured before the autouse stub replaces it.
_REAL_SCOPED_READ = sb._scoped_read
from apps.credentials.models import CredentialProfile
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


def _collector(name, account, status="active"):
    return Collector.objects.create(name=name, collector_type="remote", status=status,
                                    nats_account=account, api_key_hash=name)


def _snmp_profile():
    p = CredentialProfile.objects.create(name="snmp", snmpv2c_enabled=True)
    p.vault_path = p.default_vault_path()   # netpulse/credentials/{pk}
    p.save()
    return p


def _device(host, ip, profile, collector=None, site=None):
    return Device.objects.create(hostname=host, ip_address=ip, platform="ios_xe",
                                 status="active", credential_profile=profile,
                                 collector=collector, site=site)


@pytest.fixture(autouse=True)
def _stub_read(monkeypatch):
    # Never hit a real OpenBao; the scoped read returns a marker secret.
    monkeypatch.setattr(sb, "_scoped_read", lambda path: {"ssh_password": "s3cr3t", "snmp_community": "ro"})


@pytest.fixture
def caplog_audit(caplog):
    caplog.set_level(logging.INFO, logger="collectors.secret_broker.audit")
    return caplog


def _last_audit(caplog):
    recs = [r for r in caplog.records if r.name == "collectors.secret_broker.audit"]
    return recs[-1].getMessage() if recs else ""


# ── Negative (priority) ──────────────────────────────────────────────────────

class TestDenials:
    def test_unknown_account_denied(self, caplog_audit):
        r = sb.fetch("nope-account", {"device_id": 1})
        assert r == {"ok": False, "error": "unauthorized"}
        assert "decision=deny" in _last_audit(caplog_audit) and "unknown_or_revoked" in _last_audit(caplog_audit)

    def test_revoked_collector_denied(self, caplog_audit):
        a = _collector("A", "collector-A", status="revoked")
        d = _device("d", "10.0.0.1", _snmp_profile(), collector=a)
        assert sb.fetch("collector-A", {"device_id": d.id})["error"] == "unauthorized"

    @pytest.mark.parametrize("bad", [None, "abc", 0, -1, "1; DROP", 1.5, [], {}])
    def test_malformed_device_id_denied(self, bad, caplog_audit):
        _collector("A", "collector-A")
        r = sb.fetch("collector-A", {"device_id": bad})
        assert r["error"] == "bad_request"
        assert "malformed_device_id" in _last_audit(caplog_audit)

    def test_device_not_found_denied(self, caplog_audit):
        _collector("A", "collector-A")
        assert sb.fetch("collector-A", {"device_id": 999999})["error"] == "not_found"

    def test_device_owned_by_another_collector_denied(self, caplog_audit):
        a = _collector("A", "collector-A")
        b = _collector("B", "collector-B")
        dev_b = _device("db", "10.0.0.2", _snmp_profile(), collector=b)
        r = sb.fetch("collector-A", {"device_id": dev_b.id})   # A asks for B's device
        assert r["error"] == "forbidden"
        assert "not_owned" in _last_audit(caplog_audit)

    def test_device_resolves_to_central_denied(self):
        # No collector, no site default, no global default → owner None → deny.
        a = _collector("A", "collector-A")
        loose = _device("loose", "10.0.0.3", _snmp_profile())
        assert sb.fetch("collector-A", {"device_id": loose.id})["error"] == "forbidden"

    def test_device_owned_by_local_default_denied(self):
        # Device resolves to the LOCAL/central default collector → a remote
        # collector must not get it.
        a = _collector("A", "collector-A")
        Collector.objects.create(name="local", collector_type="local", is_default=True,
                                 status="active", api_key_hash="local")
        loose = _device("loose2", "10.0.0.4", _snmp_profile())
        assert sb.fetch("collector-A", {"device_id": loose.id})["error"] == "forbidden"

    def test_bad_vault_path_shape_denied(self, caplog_audit):
        a = _collector("A", "collector-A")
        p = _snmp_profile()
        p.vault_path = "secret/root/../escape"   # not the device-cred shape
        p.save()
        d = _device("d", "10.0.0.5", p, collector=a)
        r = sb.fetch("collector-A", {"device_id": d.id})
        assert r["error"] == "no_credentials"
        assert "bad_vault_path" in _last_audit(caplog_audit)

    def test_missing_credential_profile_denied(self):
        a = _collector("A", "collector-A")
        d = Device.objects.create(hostname="np", ip_address="10.0.0.6", platform="ios_xe",
                                  status="active", collector=a)
        assert sb.fetch("collector-A", {"device_id": d.id})["error"] == "no_credentials"

    def test_read_error_degrades_to_deny(self, monkeypatch, caplog_audit):
        a = _collector("A", "collector-A")
        d = _device("d", "10.0.0.7", _snmp_profile(), collector=a)
        monkeypatch.setattr(sb, "_scoped_read", lambda p: (_ for _ in ()).throw(RuntimeError("vault down")))
        r = sb.fetch("collector-A", {"device_id": d.id})
        assert r["error"] == "fetch_failed"
        assert "read_error:RuntimeError" in _last_audit(caplog_audit)


# ── Confused-deputy: body fields are IGNORED; only the transport identity counts ──

class TestConfusedDeputy:
    def test_body_collector_id_cannot_grant_access(self, caplog_audit):
        # A authenticates; the body claims to be B and targets B's device. The
        # broker must scope to A (authenticated) and DENY — the body is ignored.
        a = _collector("A", "collector-A")
        b = _collector("B", "collector-B")
        dev_b = _device("db", "10.0.0.8", _snmp_profile(), collector=b)
        r = sb.fetch("collector-A", {
            "device_id": dev_b.id,
            "collector_id": b.id,                 # ignored
            "account": "collector-B",             # ignored
            "vault_path": "netpulse/credentials/1",  # ignored
        })
        assert r["error"] == "forbidden"

    def test_body_vault_path_is_never_read(self, monkeypatch):
        # The broker reads the path IT computes, never the body's vault_path.
        a = _collector("A", "collector-A")
        prof = _snmp_profile()
        d = _device("d", "10.0.0.9", prof, collector=a)
        seen = {}
        monkeypatch.setattr(sb, "_scoped_read", lambda path: seen.setdefault("path", path) or {"x": 1})
        sb.fetch("collector-A", {"device_id": d.id, "vault_path": "netpulse/credentials/99999"})
        assert seen["path"] == prof.vault_path      # computed, not the body's


# ── Happy path + narrowing ───────────────────────────────────────────────────

class TestAllow:
    def test_owned_device_allows_and_audits(self, caplog_audit):
        a = _collector("A", "collector-A")
        d = _device("d", "10.0.0.10", _snmp_profile(), collector=a)
        r = sb.fetch("collector-A", {"device_id": d.id})
        assert r["ok"] is True and r["secret"]["ssh_password"] == "s3cr3t"
        assert "decision=allow" in _last_audit(caplog_audit)

    def test_owned_via_site_default_collector(self):
        a = _collector("A", "collector-A")
        site = Site.objects.create(name="S", default_collector=a)
        d = _device("d", "10.0.0.11", _snmp_profile(), site=site)
        assert sb.fetch("collector-A", {"device_id": d.id})["ok"] is True

    def test_protocol_narrowing(self):
        a = _collector("A", "collector-A")
        d = _device("d", "10.0.0.12", _snmp_profile(), collector=a)
        r = sb.fetch("collector-A", {"device_id": d.id, "protocol": "snmp"})
        assert set(r["secret"]) == {"snmp_community"}     # ssh_* narrowed out

    def _unused(self): ...


class TestFailClosed:
    """A production broker with no scoped AppRole must NOT silently escalate to
    the platform reader — it refuses to start, and refuses to read."""

    def _no_approle(self, monkeypatch):
        monkeypatch.delenv("BROKER_APPROLE_ROLE_ID", raising=False)
        monkeypatch.delenv("BROKER_APPROLE_SECRET_ID", raising=False)

    def test_prod_without_approle_refuses_to_start(self, settings, monkeypatch):
        settings.BROKER_REQUIRE_APPROLE = True
        self._no_approle(monkeypatch)
        with pytest.raises(RuntimeError, match="refuses to start"):
            sb.check_broker_config()

    def test_prod_without_approle_read_fails_closed(self, settings, monkeypatch):
        # Defence-in-depth: even bypassing the startup check, the read refuses.
        settings.BROKER_REQUIRE_APPROLE = True
        self._no_approle(monkeypatch)
        monkeypatch.setattr(sb, "_scoped_read", _REAL_SCOPED_READ)  # un-stub
        with pytest.raises(RuntimeError, match="refusing platform-reader fallback"):
            sb._scoped_read("netpulse/credentials/1")

    def test_dev_without_approle_is_allowed(self, settings, monkeypatch):
        settings.BROKER_REQUIRE_APPROLE = False
        self._no_approle(monkeypatch)
        sb.check_broker_config()  # must NOT raise in dev

    def test_prod_with_approle_starts(self, settings, monkeypatch):
        settings.BROKER_REQUIRE_APPROLE = True
        monkeypatch.setenv("BROKER_APPROLE_ROLE_ID", "rid")
        monkeypatch.setenv("BROKER_APPROLE_SECRET_ID", "sid")
        sb.check_broker_config()  # configured → fine


class TestAuditAlways:
    def test_every_request_audits(self, caplog_audit):
        # Both an allow and a deny must each leave exactly one audit line.
        a = _collector("A", "collector-A")
        d = _device("d", "10.0.0.13", _snmp_profile(), collector=a)
        sb.fetch("collector-A", {"device_id": d.id})       # allow
        sb.fetch("collector-A", {"device_id": 999999})     # deny
        decisions = [r.getMessage() for r in caplog_audit.records
                     if r.name == "collectors.secret_broker.audit"]
        assert sum("decision=allow" in m for m in decisions) >= 1
        assert sum("decision=deny" in m for m in decisions) >= 1
