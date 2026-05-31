import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def advisories_dir(tmp_path):
    jun = tmp_path / "juniper"; jun.mkdir()
    (jun / "a.yaml").write_text(
        "advisories:\n"
        "  - id: JSA1\n"
        "    cve_ids: [CVE-2024-1111]\n"
        "    title: Junos thing\n"
        "    description: A junos bug.\n"
        "    severity: high\n"
        "    cvss_score: 7.5\n"
        "    published: 2024-01-10\n"
        "    url: https://example.com/JSA1\n"
        "    affected: {vendor: juniper, platforms: [junos]}\n"
    )
    ar = tmp_path / "arista"; ar.mkdir()
    (ar / "b.yaml").write_text(
        "advisories:\n"
        "  - id: SA1\n"
        "    title: EOS thing (no cve id)\n"
        "    severity: medium\n"
        "    affected: {vendor: arista, platforms: [eos]}\n"
        "  - id: BAD\n"
        "    severity: not-a-severity\n"   # skipped
    )
    return str(tmp_path)


class TestCommunityAdvisories:
    def test_load_files(self, advisories_dir):
        from apps.cve.community import load_advisory_files
        advs = load_advisory_files(advisories_dir)
        assert len(advs) == 3  # 1 juniper + 2 arista (incl. the bad one)

    def test_sync_upserts_cves(self, advisories_dir):
        from apps.cve.community import sync_advisories
        from apps.cve.models import CVE
        s = sync_advisories(advisories_dir)
        assert s["cves_upserted"] == 2 and s["skipped"] == 1
        assert CVE.objects.filter(cve_id="CVE-2024-1111").exists()  # cve_ids takes precedence
        assert CVE.objects.filter(cve_id="SA1").exists()            # falls back to advisory id
        assert not CVE.objects.filter(cve_id="BAD").exists()

    def test_correlates_matching_devices(self, advisories_dir):
        from apps.cve.community import sync_advisories
        from apps.cve.models import DeviceCVE
        from apps.devices.models import Device
        d_jun = Device.objects.create(hostname="mx", ip_address="10.0.0.1", platform="junos", status="active")
        d_eos = Device.objects.create(hostname="ar", ip_address="10.0.0.2", platform="eos", status="active")
        Device.objects.create(hostname="cat", ip_address="10.0.0.3", platform="ios_xe", status="active")
        s = sync_advisories(advisories_dir)
        assert s["device_links"] == 2
        assert DeviceCVE.objects.filter(device=d_jun, cve__cve_id="CVE-2024-1111").exists()
        assert DeviceCVE.objects.filter(device=d_eos, cve__cve_id="SA1").exists()

    def test_idempotent(self, advisories_dir):
        from apps.cve.community import sync_advisories
        from apps.cve.models import CVE
        from apps.devices.models import Device
        Device.objects.create(hostname="mx", ip_address="10.0.0.1", platform="junos", status="active")
        sync_advisories(advisories_dir)
        sync_advisories(advisories_dir)  # second run shouldn't duplicate
        assert CVE.objects.filter(cve_id="CVE-2024-1111").count() == 1

    def test_command_runs(self, advisories_dir):
        from django.core.management import call_command
        call_command("load_community_advisories", "--path", advisories_dir)

    def test_shipped_advisories_parse(self, settings):
        # When the repo advisories are mounted, they must parse in-schema.
        import os
        import pytest as _pytest
        from apps.cve.community import load_advisory_files
        d = getattr(settings, "COMMUNITY_ADVISORIES_DIR", "/app/advisories")
        if not os.path.isdir(d):
            _pytest.skip("advisories dir not mounted")
        advs = load_advisory_files(d)
        # The dir may exist but be empty (e.g. only services/api is mounted in
        # the test harness, not the repo-root advisories/). Skip rather than
        # fail — there's nothing to validate against.
        if not advs:
            _pytest.skip("no advisory files found")
        assert all(a.get("severity") for a in advs)
