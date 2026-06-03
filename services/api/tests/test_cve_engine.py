"""Tests for the CVE engine: NVD parsing, version matching, correlation, sync."""
import pytest

from apps.cve import cisa, nvd, sync
from apps.cve.models import CVE, CVEFeedSettings, DeviceCVE
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Sample NVD payloads ───────────────────────────────────────────────────────

def nvd_cve(cve_id="CVE-2024-20001", *, product="ios_xe", base_score=8.6,
            severity="HIGH", start_incl="17.3.0", end_excl="17.6.0", exact=None):
    cpe_match = {
        "vulnerable": True,
        "criteria": f"cpe:2.3:o:cisco:{product}:{exact or '*'}:*:*:*:*:*:*:*",
    }
    if exact is None:
        if start_incl:
            cpe_match["versionStartIncluding"] = start_incl
        if end_excl:
            cpe_match["versionEndExcluding"] = end_excl
    return {
        "id": cve_id,
        "descriptions": [{"lang": "en", "value": "A vulnerability in Cisco IOS XE."}],
        "metrics": {
            "cvssMetricV31": [{
                "type": "Primary",
                "cvssData": {
                    "baseScore": base_score, "baseSeverity": severity,
                    "vectorString": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                },
            }],
        },
        "configurations": [{"nodes": [{"cpeMatch": [cpe_match]}]}],
        "references": [{"url": "https://example.com/adv"}],
        "published": "2024-01-01T00:00:00.000",
        "lastModified": "2024-02-01T00:00:00.000",
    }


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_basic_fields(self):
        p = nvd.parse_cve(nvd_cve())
        assert p["cve_id"] == "CVE-2024-20001"
        assert p["severity"] == "high"
        assert p["cvss_score"] == 8.6
        assert p["cvss_vector"].startswith("CVSS:3.1")
        assert p["source"] == "nvd"
        assert p["source_url"].endswith("CVE-2024-20001")
        assert p["published_at"] is not None

    def test_parse_cpe_configs_and_platforms(self):
        p = nvd.parse_cve(nvd_cve())
        assert p["affected_platforms"] == ["ios_xe"]
        assert len(p["cpe_configs"]) == 1
        c = p["cpe_configs"][0]
        assert c["platform"] == "ios_xe"
        assert c["version_start_including"] == "17.3.0"
        assert c["version_end_excluding"] == "17.6.0"

    def test_unknown_product_ignored(self):
        p = nvd.parse_cve(nvd_cve(product="some_random_router"))
        assert p["affected_platforms"] == []
        assert p["cpe_configs"] == []

    def test_cvss_v2_fallback(self):
        cve = {
            "id": "CVE-2019-1",
            "descriptions": [{"lang": "en", "value": "old"}],
            "metrics": {"cvssMetricV2": [{"type": "Primary", "baseSeverity": "MEDIUM",
                                          "cvssData": {"baseScore": 5.0, "vectorString": "AV:N"}}]},
        }
        p = nvd.parse_cve(cve)
        assert p["severity"] == "medium" and p["cvss_score"] == 5.0


# ── NVD fetch (HTTP) ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _Session:
    """Fake requests.Session returning queued responses per .get() call."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.headers_seen = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(params)
        self.headers_seen.append(headers or {})
        return self._responses.pop(0)


class TestNvdFetch:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        # Default: a valid key is configured. Individual tests override.
        monkeypatch.setattr(nvd, "_resolve_api_key", lambda: "valid-key")

    def test_uses_virtual_match_string(self):
        page = {"vulnerabilities": [{"cve": nvd_cve("CVE-2024-1")}], "totalResults": 1}
        sess = _Session([_Resp(200, page)])
        out = list(nvd.fetch_platform("ios_xe", session=sess, page_sleep=0))
        assert len(out) == 1
        assert sess.calls[0]["virtualMatchString"] == "cpe:2.3:o:cisco:ios_xe"

    def test_pagination(self):
        p1 = {"vulnerabilities": [{"cve": nvd_cve("CVE-2024-1")}], "totalResults": 2}
        p2 = {"vulnerabilities": [{"cve": nvd_cve("CVE-2024-2")}], "totalResults": 2}
        sess = _Session([_Resp(200, p1), _Resp(200, p2)])
        out = list(nvd.fetch_platform("ios_xe", session=sess, page_sleep=0))
        assert {c["id"] for c in out} == {"CVE-2024-1", "CVE-2024-2"}

    def test_404_with_key_falls_back_to_keyless(self):
        # 404 while a key is sent → invalid key → retry the page keyless (200).
        page = {"vulnerabilities": [{"cve": nvd_cve("CVE-2024-1")}], "totalResults": 1}
        sess = _Session([_Resp(404), _Resp(200, page)])
        out = list(nvd.fetch_platform("fortios", session=sess, page_sleep=0))
        assert len(out) == 1
        assert "apiKey" in sess.headers_seen[0] and "apiKey" not in sess.headers_seen[1]

    def test_404_keyless_skips_platform(self, monkeypatch):
        monkeypatch.setattr(nvd, "_resolve_api_key", lambda: "")
        sess = _Session([_Resp(404, text="not found")])
        assert list(nvd.fetch_platform("fortios", session=sess, page_sleep=0)) == []

    def test_403_raises_auth_error(self):
        sess = _Session([_Resp(403, text="forbidden")])
        with pytest.raises(nvd.NvdAuthError):
            list(nvd.fetch_platform("ios_xe", session=sess, page_sleep=0))

    def test_503_skips_platform(self):
        sess = _Session([_Resp(503, text="down")])
        assert list(nvd.fetch_platform("ios", session=sess, page_sleep=0)) == []

    def test_429_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("apps.cve.nvd.time.sleep", lambda *_: None)
        page = {"vulnerabilities": [{"cve": nvd_cve("CVE-2024-1")}], "totalResults": 1}
        sess = _Session([_Resp(429), _Resp(200, page)])
        out = list(nvd.fetch_platform("ios_xe", session=sess, page_sleep=0))
        assert len(out) == 1

    def test_unknown_platform_yields_nothing(self):
        assert list(nvd.fetch_platform("not_a_platform", page_sleep=0)) == []


# ── Version matching ──────────────────────────────────────────────────────────

class TestVersionMatching:
    def test_parse_version_ordering(self):
        assert nvd.parse_version("17.3.1") < nvd.parse_version("17.3.10")
        assert nvd.parse_version("16.12.4") < nvd.parse_version("17.3.1")
        assert nvd.parse_version("17.3") < nvd.parse_version("17.3a")

    def test_in_range(self):
        cfg = nvd.parse_cve(nvd_cve())["cpe_configs"][0]
        assert nvd.version_matches("17.3.1", cfg) is True
        assert nvd.version_matches("17.5.9", cfg) is True

    def test_below_range(self):
        cfg = nvd.parse_cve(nvd_cve())["cpe_configs"][0]
        assert nvd.version_matches("16.12.1", cfg) is False

    def test_above_range(self):
        cfg = nvd.parse_cve(nvd_cve())["cpe_configs"][0]
        assert nvd.version_matches("17.6.0", cfg) is False  # end excluded
        assert nvd.version_matches("18.1.1", cfg) is False

    def test_exact_version_prefix(self):
        cfg = nvd.parse_cve(nvd_cve(exact="17.3"))["cpe_configs"][0]
        assert nvd.version_matches("17.3.1", cfg) is True
        assert nvd.version_matches("17.4.1", cfg) is False


# ── evaluate() / correlate() ──────────────────────────────────────────────────

class TestCorrelation:
    def _cve(self, **kw):
        return sync.upsert_cve(nvd.parse_cve(nvd_cve(**kw)))

    def test_vulnerable_in_range(self):
        dev = Device.objects.create(hostname="r1", ip_address="10.0.0.1",
                                    platform="ios_xe", os_version="17.3.1")
        cve = self._cve()
        sync.correlate([cve])
        link = DeviceCVE.objects.get(device=dev, cve=cve)
        assert link.match_type == DeviceCVE.MatchType.VERSION_RANGE

    def test_not_applicable_above_range(self):
        Device.objects.create(hostname="r2", ip_address="10.0.0.2",
                              platform="ios_xe", os_version="18.1.1")
        cve = self._cve()
        s = sync.correlate([cve])
        assert DeviceCVE.objects.count() == 0
        assert s["not_applicable"] == 1

    def test_unverified_when_version_unknown(self):
        dev = Device.objects.create(hostname="r3", ip_address="10.0.0.3",
                                    platform="ios_xe", os_version="")
        cve = self._cve()
        sync.correlate([cve])
        link = DeviceCVE.objects.get(device=dev, cve=cve)
        assert link.match_type == DeviceCVE.MatchType.UNVERIFIED

    def test_keyword_match_without_cpe(self):
        dev = Device.objects.create(hostname="r4", ip_address="10.0.0.4",
                                    platform="ios_xe", os_version="17.3.1")
        # CVE whose CPE product is unknown, but keyword-associated to ios_xe.
        parsed = nvd.parse_cve(nvd_cve(product="mystery"))
        parsed["affected_platforms"] = ["ios_xe"]
        cve = sync.upsert_cve(parsed)
        sync.correlate([cve])
        link = DeviceCVE.objects.get(device=dev, cve=cve)
        assert link.match_type == DeviceCVE.MatchType.KEYWORD

    def test_correlate_skips_other_platforms(self):
        Device.objects.create(hostname="fw", ip_address="10.0.0.5",
                              platform="fortios", os_version="7.2.1")
        cve = self._cve()  # ios_xe only
        sync.correlate([cve])
        assert DeviceCVE.objects.count() == 0

    def test_correlate_preserves_is_patched(self):
        dev = Device.objects.create(hostname="r6", ip_address="10.0.0.6",
                                    platform="ios_xe", os_version="17.3.1")
        cve = self._cve()
        link = DeviceCVE.objects.create(device=dev, cve=cve, is_patched=True)
        sync.correlate([cve])
        link.refresh_from_db()
        assert link.is_patched is True  # re-correlation never un-patches


# ── upsert ────────────────────────────────────────────────────────────────────

class TestUpsert:
    def test_upsert_merges_platforms(self):
        p1 = nvd.parse_cve(nvd_cve())
        p1["affected_platforms"] = ["ios_xe"]
        sync.upsert_cve(p1)
        p2 = nvd.parse_cve(nvd_cve())
        p2["affected_platforms"] = ["ios"]
        cve = sync.upsert_cve(p2)
        assert set(cve.affected_platforms) >= {"ios", "ios_xe"}

    def test_upsert_preserves_kev_flag(self):
        cve = sync.upsert_cve(nvd.parse_cve(nvd_cve()))
        CVE.objects.filter(pk=cve.pk).update(cisa_kev=True)
        sync.upsert_cve(nvd.parse_cve(nvd_cve()))  # re-ingest
        cve.refresh_from_db()
        assert cve.cisa_kev is True


# ── run_sync (NVD mocked) ─────────────────────────────────────────────────────

class TestRunSync:
    def test_full_sync(self, monkeypatch):
        Device.objects.create(hostname="r1", ip_address="10.0.0.1",
                              platform="ios_xe", os_version="17.3.1")

        def fake_fetch(platform, **kw):
            yield nvd_cve("CVE-2024-20001")
            yield nvd_cve("CVE-2024-20002", end_excl="17.4.0")

        monkeypatch.setattr(nvd, "fetch_platform", fake_fetch)
        # disable optional feeds for a deterministic run
        s = CVEFeedSettings.load()
        s.cisa_kev_enabled = False
        s.save()
        monkeypatch.setattr("apps.cve.psirt.fetch_advisories", lambda platforms, **k: iter(()))

        summary = sync.run_sync(page_sleep=0)
        assert summary["cves_upserted"] == 2
        assert "ios_xe" in summary["platforms"]
        assert summary["links_created"] == 2
        assert CVE.objects.count() == 2
        assert DeviceCVE.objects.count() == 2

        s.refresh_from_db()
        assert s.last_sync_status == "ok"
        assert s.last_synced_at is not None

    def test_sync_no_relevant_platforms(self, monkeypatch):
        # Device on a platform with no NVD keyword mapping → nothing fetched.
        Device.objects.create(hostname="x", ip_address="10.9.9.9", platform="other")
        called = []
        monkeypatch.setattr(nvd, "fetch_platform", lambda platform, **k: called.append(platform) or iter(()))
        monkeypatch.setattr("apps.cve.psirt.fetch_advisories", lambda p, **k: iter(()))
        s = CVEFeedSettings.load(); s.cisa_kev_enabled = False; s.save()
        summary = sync.run_sync(page_sleep=0)
        assert summary["platforms"] == []
        assert summary["cves_upserted"] == 0


# ── CISA KEV ──────────────────────────────────────────────────────────────────

class TestCisaKev:
    def test_flag_known_exploited(self, monkeypatch):
        sync.upsert_cve(nvd.parse_cve(nvd_cve("CVE-2024-20001")))
        sync.upsert_cve(nvd.parse_cve(nvd_cve("CVE-2024-20002")))

        class FakeResp:
            def raise_for_status(self): pass
            def json(self):
                return {"vulnerabilities": [{"cveID": "CVE-2024-20001"}]}

        class FakeSession:
            def get(self, *a, **k): return FakeResp()

        flagged = cisa.flag_known_exploited(session=FakeSession())
        assert flagged == 1
        assert CVE.objects.get(cve_id="CVE-2024-20001").cisa_kev is True
        assert CVE.objects.get(cve_id="CVE-2024-20002").cisa_kev is False


# ── Endpoints ─────────────────────────────────────────────────────────────────

class TestCVEEngineEndpoints:
    def test_summary(self, auth_client):
        CVE.objects.create(cve_id="CVE-2024-1", description="c", severity="critical", cvss_score="9.8", cisa_kev=True)
        CVE.objects.create(cve_id="CVE-2024-2", description="h", severity="high", cvss_score="7.5")
        resp = auth_client.get("/api/cve/cves/summary/")
        assert resp.status_code == 200
        b = resp.json()
        assert b["total"] == 2 and b["critical"] == 1 and b["high"] == 1
        assert b["kev_count"] == 1
        assert "last_synced_at" in b and "affected_devices" in b

    def test_list_includes_affected_device_count(self, auth_client):
        dev = Device.objects.create(hostname="r", ip_address="10.0.0.1", platform="ios_xe")
        cve = CVE.objects.create(cve_id="CVE-2024-3", description="x", severity="high")
        DeviceCVE.objects.create(device=dev, cve=cve)
        resp = auth_client.get("/api/cve/cves/")
        row = next(r for r in resp.json()["results"] if r["cve_id"] == "CVE-2024-3")
        assert row["affected_device_count"] == 1

    def test_sync_trigger_admin(self, auth_client, monkeypatch):
        import threading
        from apps.cve import sync as sync_mod
        ran = threading.Event()
        monkeypatch.setattr(sync_mod, "run_sync", lambda *a, **k: ran.set())
        resp = auth_client.post("/api/cve/cves/sync/")
        assert resp.status_code == 202
        assert ran.wait(timeout=3) is True

    def test_sync_trigger_forbidden_for_viewer(self, viewer_client):
        resp = viewer_client.post("/api/cve/cves/sync/")
        assert resp.status_code == 403

    def test_mark_patched(self, auth_client):
        dev = Device.objects.create(hostname="r", ip_address="10.0.0.1", platform="ios_xe")
        cve = CVE.objects.create(cve_id="CVE-2024-4", description="x", severity="high")
        link = DeviceCVE.objects.create(device=dev, cve=cve)
        resp = auth_client.patch(f"/api/cve/device-cves/{link.pk}/", {"is_patched": True}, format="json")
        assert resp.status_code == 200
        link.refresh_from_db()
        assert link.is_patched is True and link.patched_at is not None

    def test_device_cve_endpoint(self, auth_client):
        dev = Device.objects.create(hostname="r", ip_address="10.0.0.1", platform="ios_xe")
        cve = CVE.objects.create(cve_id="CVE-2024-5", description="x", severity="critical", source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-5")
        DeviceCVE.objects.create(device=dev, cve=cve, match_type="version_range")
        resp = auth_client.get(f"/api/devices/{dev.pk}/cve/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["cve_id"] == "CVE-2024-5"
        assert data[0]["source_url"].endswith("CVE-2024-5")
        assert data[0]["severity"] == "critical"
