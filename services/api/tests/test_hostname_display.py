import pytest

from apps.core.hostname import hostname_display_config, strip_domain
from apps.core.models import SystemSetting
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(
        hostname="router1.dnstest.local",
        ip_address="10.0.0.1",
        platform=Device.Platform.IOS_XE,
    )


def _set_mode(mode, suffix=""):
    SystemSetting.set("hostname_display_mode", mode)
    SystemSetting.set("domain_suffix", suffix)


# ── strip_domain logic ─────────────────────────────────────────────────────────

class TestStripDomain:
    def test_suffix_match(self):
        _set_mode("strip", "dnstest.local")
        assert strip_domain("router1.dnstest.local") == "router1"

    def test_suffix_non_match(self):
        # Strip enabled with a specific suffix that does not match → unchanged.
        _set_mode("strip", "dnstest.local")
        assert strip_domain("router1.other.example") == "router1.other.example"

    def test_empty_suffix_first_dot_split(self):
        _set_mode("strip", "")
        assert strip_domain("router1.dnstest.local") == "router1"

    def test_empty_suffix_no_dot(self):
        _set_mode("strip", "")
        assert strip_domain("router1") == "router1"

    def test_strip_disabled_returns_full(self):
        _set_mode("full", "dnstest.local")
        assert strip_domain("router1.dnstest.local") == "router1.dnstest.local"

    def test_empty_hostname(self):
        _set_mode("strip", "")
        assert strip_domain("") == ""


# ── hostname_display_config fallback to settings ────────────────────────────────

class TestDisplayConfig:
    def test_falls_back_to_settings_when_unset(self, settings):
        # No SystemSetting rows → use settings defaults.
        settings.STRIP_DOMAIN_FROM_HOSTNAMES = True
        settings.DOMAIN_SUFFIX = "env.local"
        strip_enabled, suffix = hostname_display_config()
        assert strip_enabled is True
        assert suffix == "env.local"

    def test_systemsetting_overrides_settings(self, settings):
        settings.STRIP_DOMAIN_FROM_HOSTNAMES = False
        settings.DOMAIN_SUFFIX = ""
        _set_mode("strip", "db.local")
        strip_enabled, suffix = hostname_display_config()
        assert strip_enabled is True
        assert suffix == "db.local"


# ── SystemSetting get/set ───────────────────────────────────────────────────────

class TestSystemSetting:
    def test_set_and_get(self):
        SystemSetting.set("foo", "bar")
        assert SystemSetting.get("foo") == "bar"

    def test_get_default_when_missing(self):
        assert SystemSetting.get("nope", default="fallback") == "fallback"

    def test_set_is_idempotent_update(self):
        SystemSetting.set("foo", "one")
        SystemSetting.set("foo", "two")
        assert SystemSetting.get("foo") == "two"
        assert SystemSetting.objects.filter(key="foo").count() == 1


# ── Device.display_hostname ─────────────────────────────────────────────────────

class TestDeviceDisplayHostname:
    def test_reflects_systemsetting_strip(self, device):
        _set_mode("strip", "dnstest.local")
        assert device.display_hostname == "router1"
        # The stored hostname is unchanged.
        assert device.hostname == "router1.dnstest.local"

    def test_reflects_systemsetting_full(self, device):
        _set_mode("full", "dnstest.local")
        assert device.display_hostname == "router1.dnstest.local"


# ── API endpoint ────────────────────────────────────────────────────────────────

class TestHostnameDisplayEndpoint:
    URL = "/api/settings/hostname-display/"

    def test_get_default(self, auth_client):
        resp = auth_client.get(self.URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] in ("strip", "full")
        assert "domain_suffix" in body

    def test_put_requires_admin(self, api_client):
        # Unauthenticated → 401.
        resp = api_client.put(self.URL, {"mode": "strip", "domain_suffix": "x.local"}, format="json")
        assert resp.status_code == 401

    def test_put_saves_state(self, auth_client):
        resp = auth_client.put(
            self.URL, {"mode": "strip", "domain_suffix": "dnstest.local"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json() == {"mode": "strip", "domain_suffix": "dnstest.local"}
        assert SystemSetting.get("hostname_display_mode") == "strip"
        assert SystemSetting.get("domain_suffix") == "dnstest.local"

    def test_put_rejects_bad_mode(self, auth_client):
        resp = auth_client.put(self.URL, {"mode": "nonsense"}, format="json")
        assert resp.status_code == 400

    def test_put_then_device_display_reflects(self, auth_client, device):
        auth_client.put(self.URL, {"mode": "strip", "domain_suffix": "dnstest.local"}, format="json")
        device.refresh_from_db()
        assert device.display_hostname == "router1"
