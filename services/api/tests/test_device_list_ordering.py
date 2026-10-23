"""Device list: server-side page size, and ordering semantics (case-insensitive
text, NULLs last in both directions, ping via the cached InfluxDB summary)."""
import pytest

from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def site():
    return Site.objects.create(name="DC-1", location="New York")


class TestDeviceListOrdering:
    URL = "/api/devices/"

    def _names(self, resp):
        assert resp.status_code == 200
        return [d["hostname"] for d in resp.json()["results"]]

    def test_page_size_param_honoured_and_capped(self, auth_client):
        for i in range(30):
            Device.objects.create(hostname=f"sw-{i:02d}", ip_address=f"10.9.0.{i + 1}")
        assert len(auth_client.get(self.URL, {"page_size": 25}).json()["results"]) == 25
        assert len(auth_client.get(self.URL, {"page_size": 10}).json()["results"]) == 10
        # max_page_size=500 caps silly values instead of erroring.
        assert len(auth_client.get(self.URL, {"page_size": 100000}).json()["results"]) == 30

    def test_vendor_sort_is_case_insensitive(self, auth_client):
        Device.objects.create(hostname="a", ip_address="10.9.1.1", vendor="juniper")
        Device.objects.create(hostname="b", ip_address="10.9.1.2", vendor="Juniper")
        Device.objects.create(hostname="c", ip_address="10.9.1.3", vendor="aruba")
        vendors = [d["vendor"] for d in auth_client.get(self.URL, {"ordering": "vendor"}).json()["results"]]
        assert vendors[0] == "aruba"
        assert {v.lower() for v in vendors[1:]} == {"juniper"}

    def test_nulls_last_both_directions(self, auth_client, site):
        Device.objects.create(hostname="no-site", ip_address="10.9.2.1")
        Device.objects.create(hostname="has-site", ip_address="10.9.2.2", site=site)
        assert self._names(auth_client.get(self.URL, {"ordering": "site__name"}))[-1] == "no-site"
        assert self._names(auth_client.get(self.URL, {"ordering": "-site__name"}))[-1] == "no-site"

    def test_compliance_desc_puts_unchecked_last(self, auth_client):
        from apps.compliance.models import DeviceComplianceScore
        lo = Device.objects.create(hostname="lo", ip_address="10.9.3.1")
        hi = Device.objects.create(hostname="hi", ip_address="10.9.3.2")
        Device.objects.create(hostname="unchecked", ip_address="10.9.3.3")
        DeviceComplianceScore.objects.create(device=lo, score=40)
        DeviceComplianceScore.objects.create(device=hi, score=95)
        assert self._names(auth_client.get(self.URL, {"ordering": "-compliance_score"})) == ["hi", "lo", "unchecked"]

    def test_ping_sort_uses_ping_summary(self, auth_client, monkeypatch):
        from django.core.cache import cache
        fast = Device.objects.create(hostname="fast", ip_address="10.9.4.1")
        slow = Device.objects.create(hostname="slow", ip_address="10.9.4.2")
        Device.objects.create(hostname="no-ping", ip_address="10.9.4.3")
        cache.delete("ping_summary")
        monkeypatch.setattr("apps.devices.metrics_influx.query_ping_summary", lambda: [
            {"device_id": fast.id, "current_ms": 1.5},
            {"device_id": slow.id, "current_ms": 42.0},
        ])
        assert self._names(auth_client.get(self.URL, {"ordering": "ping"})) == ["fast", "slow", "no-ping"]
        assert self._names(auth_client.get(self.URL, {"ordering": "-ping"})) == ["slow", "fast", "no-ping"]
        cache.delete("ping_summary")
