"""Site server (agent) + service-check up/down counts on the list & detail APIs.

Mirrors the existing device up/down breakdown (see test_devices.py). Servers link
to a site via their Device (Agent.device → Device.site); service checks link to
the site directly (ServiceCheck.site)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.agents.models import AGENT_ONLINE_SECONDS, Agent
from apps.checks.models import ServiceCheck
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def site():
    return Site.objects.create(name="DC-counts", location="NYC")


def _device(site, host, ip):
    return Device.objects.create(
        hostname=host, ip_address=ip, platform=Device.Platform.IOS_XE, site=site)


def _agent(site, host, ip, *, status=Agent.Status.ACTIVE, last_seen="fresh"):
    dev = _device(site, host, ip)
    if last_seen == "fresh":
        seen = timezone.now()
    elif last_seen == "stale":
        seen = timezone.now() - timedelta(seconds=AGENT_ONLINE_SECONDS + 60)
    else:
        seen = last_seen  # None or explicit datetime
    return Agent.objects.create(hostname=host, device=dev, status=status, last_seen=seen)


def _list_row(auth_client, site):
    rows = auth_client.get("/api/sites/").json()["results"]
    return next(r for r in rows if r["id"] == site.pk)


class TestServerCounts:
    def test_online_offline_and_revoked(self, auth_client, site):
        # up: active + fresh
        _agent(site, "srv-up", "10.1.0.1")
        # down: active but stale heartbeat
        _agent(site, "srv-stale", "10.1.0.2", last_seen="stale")
        # down: active but never checked in (NULL last_seen)
        _agent(site, "srv-null", "10.1.0.3", last_seen=None)
        # down: inactive
        _agent(site, "srv-inactive", "10.1.0.4", status=Agent.Status.INACTIVE)
        # excluded entirely: revoked
        _agent(site, "srv-revoked", "10.1.0.5", status=Agent.Status.REVOKED)
        # an agent at a DIFFERENT site must not leak into these counts
        other = Site.objects.create(name="DC-other")
        _agent(other, "srv-elsewhere", "10.9.0.1")

        row = _list_row(auth_client, site)
        assert (row["server_count"], row["servers_up"], row["servers_down"]) == (4, 1, 3)
        # up + down accounts for every (non-revoked) server — no gap
        assert row["servers_up"] + row["servers_down"] == row["server_count"]

        body = auth_client.get(f"/api/sites/{site.pk}/").json()
        assert (body["server_count"], body["servers_up"], body["servers_down"]) == (4, 1, 3)

    def test_zero_servers(self, auth_client, site):
        row = _list_row(auth_client, site)
        assert (row["server_count"], row["servers_up"], row["servers_down"]) == (0, 0, 0)


class TestCheckCounts:
    def test_pass_fail_breakdown(self, auth_client, site):
        S = ServiceCheck.Status
        ServiceCheck.objects.create(name="up-1", check_type="tcp", host="h", site=site, current_status=S.UP)
        ServiceCheck.objects.create(name="up-2", check_type="tcp", host="h", site=site, current_status=S.UP)
        ServiceCheck.objects.create(name="down-1", check_type="tcp", host="h", site=site, current_status=S.DOWN)
        ServiceCheck.objects.create(name="degraded-1", check_type="tcp", host="h", site=site, current_status=S.DEGRADED)
        ServiceCheck.objects.create(name="unknown-1", check_type="tcp", host="h", site=site, current_status=S.UNKNOWN)
        # inactive checks are excluded from every count
        ServiceCheck.objects.create(name="paused", check_type="tcp", host="h", site=site,
                                    current_status=S.UP, is_active=False)
        # a check at another site must not leak in
        other = Site.objects.create(name="DC-checks-other")
        ServiceCheck.objects.create(name="elsewhere", check_type="tcp", host="h", site=other, current_status=S.UP)

        row = _list_row(auth_client, site)
        # total = active checks (incl. unknown); up = UP; down = DOWN + DEGRADED
        assert (row["check_count"], row["checks_up"], row["checks_down"]) == (5, 2, 2)

        body = auth_client.get(f"/api/sites/{site.pk}/").json()
        assert (body["check_count"], body["checks_up"], body["checks_down"]) == (5, 2, 2)

    def test_zero_checks(self, auth_client, site):
        row = _list_row(auth_client, site)
        assert (row["check_count"], row["checks_up"], row["checks_down"]) == (0, 0, 0)


def test_create_response_has_zeroed_counts(auth_client):
    """The create response serializes a non-annotated instance — the fallbacks
    must return 0 (not error) for a brand-new empty site."""
    resp = auth_client.post("/api/sites/", {"name": "Fresh-Site"}, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    for key in ("server_count", "servers_up", "servers_down",
                "check_count", "checks_up", "checks_down"):
        assert body[key] == 0
