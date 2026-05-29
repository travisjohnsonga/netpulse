import pytest
from apps.devices.models import Device
from apps.lifecycle.models import LifecycleMilestone

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def device():
    return Device.objects.create(hostname="rtr-01", ip_address="10.0.0.1", vendor="Cisco")


@pytest.fixture
def milestone(device):
    return LifecycleMilestone.objects.create(
        device=device,
        milestone_type="eos",
        milestone_date="2025-06-01",
        source="Cisco Published",
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestLifecycleMilestoneEndpoints:
    def test_list_milestones(self, auth_client, milestone):
        resp = auth_client.get("/api/lifecycle/milestones/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_milestone(self, auth_client, device):
        resp = auth_client.post("/api/lifecycle/milestones/", {
            "device": device.pk,
            "milestone_type": "eol",
            "milestone_date": "2027-12-31",
            "source": "Cisco EoL Notice",
        })
        assert resp.status_code == 201
        assert resp.json()["milestone_type"] == "eol"
        assert resp.json()["milestone_date"] == "2027-12-31"

    def test_create_milestone_includes_hostname(self, auth_client, device):
        resp = auth_client.post("/api/lifecycle/milestones/", {
            "device": device.pk,
            "milestone_type": "eosm",
            "milestone_date": "2026-03-01",
        })
        assert resp.status_code == 201
        assert resp.json()["hostname"] == "rtr-01"

    def test_retrieve_milestone(self, auth_client, milestone):
        resp = auth_client.get(f"/api/lifecycle/milestones/{milestone.pk}/")
        assert resp.status_code == 200
        assert resp.json()["milestone_type"] == "eos"

    def test_update_milestone(self, auth_client, milestone):
        resp = auth_client.patch(f"/api/lifecycle/milestones/{milestone.pk}/", {
            "milestone_date": "2025-09-01",
        })
        assert resp.status_code == 200
        assert resp.json()["milestone_date"] == "2025-09-01"

    def test_delete_milestone(self, auth_client, milestone):
        resp = auth_client.delete(f"/api/lifecycle/milestones/{milestone.pk}/")
        assert resp.status_code == 204
        assert not LifecycleMilestone.objects.filter(pk=milestone.pk).exists()

    def test_filter_by_device(self, auth_client, milestone, device):
        other = Device.objects.create(hostname="rtr-02", ip_address="10.0.0.2")
        LifecycleMilestone.objects.create(device=other, milestone_type="eos", milestone_date="2026-01-01")
        resp = auth_client.get(f"/api/lifecycle/milestones/?device={device.pk}")
        assert resp.status_code == 200
        assert all(m["device"] == device.pk for m in resp.json()["results"])

    def test_filter_by_milestone_type(self, auth_client, milestone, device):
        LifecycleMilestone.objects.create(device=device, milestone_type="eol", milestone_date="2028-01-01")
        resp = auth_client.get("/api/lifecycle/milestones/?milestone_type=eos")
        assert resp.status_code == 200
        assert all(m["milestone_type"] == "eos" for m in resp.json()["results"])

    def test_ordering_by_date(self, auth_client, milestone, device):
        LifecycleMilestone.objects.create(device=device, milestone_type="eol", milestone_date="2028-01-01")
        resp = auth_client.get("/api/lifecycle/milestones/?ordering=milestone_date")
        assert resp.status_code == 200
        dates = [m["milestone_date"] for m in resp.json()["results"]]
        assert dates == sorted(dates)

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/lifecycle/milestones/")
        assert resp.status_code == 401


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestLifecycleModel:
    def test_str(self, milestone):
        assert "rtr-01" in str(milestone)
        assert "End of Sale" in str(milestone)

    def test_unique_per_device_and_type(self, device):
        from django.db import IntegrityError
        LifecycleMilestone.objects.create(device=device, milestone_type="eoss", milestone_date="2026-01-01")
        with pytest.raises(IntegrityError):
            LifecycleMilestone.objects.create(device=device, milestone_type="eoss", milestone_date="2027-01-01")

    def test_same_type_different_devices_allowed(self):
        d1 = Device.objects.create(hostname="rtr-a", ip_address="10.1.0.1")
        d2 = Device.objects.create(hostname="rtr-b", ip_address="10.1.0.2")
        LifecycleMilestone.objects.create(device=d1, milestone_type="eol", milestone_date="2026-01-01")
        m2 = LifecycleMilestone.objects.create(device=d2, milestone_type="eol", milestone_date="2027-01-01")
        assert m2.pk is not None

    def test_milestone_type_choices(self):
        for val, _ in LifecycleMilestone.MilestoneType.choices:
            assert val in ("eos", "eosm", "eoss", "eol")

    def test_all_four_types_per_device(self, device):
        types = ["eos", "eosm", "eoss", "eol"]
        for i, t in enumerate(types):
            LifecycleMilestone.objects.create(
                device=device, milestone_type=t,
                milestone_date=f"202{5+i}-01-01",
            )
        assert LifecycleMilestone.objects.filter(device=device).count() == 4
