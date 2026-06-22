"""Tests for the ChatOps app (Phase 1 persistence/config + Phase 2 policy)."""
import pytest

from apps.chatops.models import (
    ChatOpsChannel, ChatOpsConfig, ChatOpsIdentity, ChatOpsPlatform,
    chatops_vault_path,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake_vault(monkeypatch):
    """In-memory stand-in for OpenBao so secret round-trips can be asserted.

    Patches the vault read/write the ChatOps helpers call (the autouse
    conftest guard otherwise forces the real vault closed)."""
    store: dict[str, dict] = {}

    def _write(path, data):
        clean = {k: v for k, v in data.items() if v not in (None, "")}
        store.setdefault(path, {}).update(clean)

    def _read(path):
        return dict(store.get(path, {}))

    monkeypatch.setattr("apps.credentials.vault.write_secret", _write)
    monkeypatch.setattr("apps.credentials.vault.read_secret", _read)
    return store


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """Reset the rate-throttle history before every test.

    The webhook endpoints carry the "chatops" ScopedRateThrottle (30/min). DRF
    binds SimpleRateThrottle.THROTTLE_RATES at import, so the test-settings
    DEFAULT_THROTTLE_RATES override doesn't disable it; without this, webhook
    posts accumulated across the file would eventually trip a 429 in unrelated
    tests. Clearing the (LocMem) cache per test makes each one deterministic;
    the dedicated throttle test re-enables a tiny rate against a clean cache."""
    from django.core.cache import cache
    cache.clear()
    yield


def _enable(platform):
    return ChatOpsPlatform.objects.update_or_create(
        platform=platform, defaults={"enabled": True})[0]


# ── Phase 1: gating ───────────────────────────────────────────────────────────

class TestPlatformGating:
    def test_master_off_returns_404(self, api_client, settings):
        settings.CHATOPS_ENABLED = False
        _enable("slack")
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 404

    def test_platform_row_disabled_returns_404(self, api_client, settings):
        settings.CHATOPS_ENABLED = True
        ChatOpsPlatform.objects.update_or_create(platform="slack", defaults={"enabled": False})
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 404

    def test_no_row_returns_404(self, api_client, settings):
        settings.CHATOPS_ENABLED = True
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 404

    def test_master_on_and_row_enabled_is_live(self, api_client, settings):
        settings.CHATOPS_ENABLED = True
        _enable("slack")
        payload = {"event": {"text": "help", "user": "U1", "channel": "C1"}}
        resp = api_client.post("/api/webhooks/slack/", data=payload, format="json")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]


# ── Phase 1: platform config API + secrets ────────────────────────────────────

class TestPlatformAPI:
    def test_list_returns_all_platforms(self, admin_client):
        resp = admin_client.get("/api/chatops/platforms/")
        assert resp.status_code == 200
        platforms = {row["platform"] for row in resp.json()}
        assert platforms == {"slack", "teams", "gchat", "discord", "mattermost"}

    def test_enable_and_set_display_name(self, admin_client):
        resp = admin_client.put("/api/chatops/platforms/slack/",
                                data={"enabled": True, "display_name": "spane bot"},
                                format="json")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        assert ChatOpsPlatform.objects.get(platform="slack").enabled is True

    def test_secret_round_trip_and_write_only(self, admin_client, fake_vault):
        resp = admin_client.put("/api/chatops/platforms/slack/",
                                data={"signing_secret": "s3cr3t-sign", "bot_token": "xoxb-tok"},
                                format="json")
        assert resp.status_code == 200
        body = resp.json()
        # Secret value never echoed back anywhere in the response.
        assert "s3cr3t-sign" not in resp.content.decode()
        assert "xoxb-tok" not in resp.content.decode()
        # Stored indicator shows the placeholder, not the value.
        assert body["secrets"]["signing_secret"] == "🔒 Stored securely in OpenBao"
        # Actually persisted to (fake) OpenBao.
        assert fake_vault[chatops_vault_path("slack")]["signing_secret"] == "s3cr3t-sign"

    def test_secret_not_wiped_by_settings_save(self, admin_client, fake_vault):
        admin_client.put("/api/chatops/platforms/slack/",
                         data={"signing_secret": "keep-me"}, format="json")
        admin_client.put("/api/chatops/platforms/slack/",
                         data={"display_name": "renamed"}, format="json")
        assert fake_vault[chatops_vault_path("slack")]["signing_secret"] == "keep-me"

    def test_only_platform_relevant_secret_written(self, admin_client, fake_vault):
        # 'token' belongs to mattermost, not slack — it must be ignored here.
        admin_client.put("/api/chatops/platforms/slack/",
                         data={"token": "nope"}, format="json")
        assert "token" not in fake_vault.get(chatops_vault_path("slack"), {})

    def test_get_shows_blank_when_unset(self, admin_client, fake_vault):
        resp = admin_client.get("/api/chatops/platforms/discord/")
        assert resp.json()["secrets"] == {"public_key": "", "bot_token": ""}

    def test_viewer_cannot_write(self, viewer_client):
        resp = viewer_client.put("/api/chatops/platforms/slack/",
                                 data={"enabled": True}, format="json")
        assert resp.status_code == 403

    def test_viewer_can_read(self, viewer_client):
        assert viewer_client.get("/api/chatops/platforms/").status_code == 200

    def test_unauthenticated_denied(self, api_client):
        assert api_client.get("/api/chatops/platforms/").status_code == 401

    def test_unknown_platform_404(self, admin_client):
        assert admin_client.get("/api/chatops/platforms/irc/").status_code == 404

    def test_test_action_missing_creds(self, admin_client, fake_vault):
        resp = admin_client.post("/api/chatops/platforms/slack/test/")
        assert resp.status_code == 400
        assert resp.json()["connected"] is False

    def test_test_action_with_creds(self, admin_client, fake_vault):
        admin_client.put("/api/chatops/platforms/slack/",
                         data={"signing_secret": "a", "bot_token": "b"}, format="json")
        resp = admin_client.post("/api/chatops/platforms/slack/test/")
        assert resp.status_code == 200
        assert resp.json()["connected"] is True


# ── Phase 1: channel CRUD ─────────────────────────────────────────────────────

class TestChannelCRUD:
    def test_create_list_update_delete(self, admin_client):
        resp = admin_client.post("/api/chatops/channels/",
                                 data={"platform": "slack", "channel_id": "C1",
                                       "name": "ops", "purpose": "query"}, format="json")
        assert resp.status_code == 201
        cid = resp.json()["id"]

        assert admin_client.get("/api/chatops/channels/").json()["count"] == 1

        upd = admin_client.patch(f"/api/chatops/channels/{cid}/",
                                 data={"enabled": False}, format="json")
        assert upd.status_code == 200 and upd.json()["enabled"] is False

        assert admin_client.delete(f"/api/chatops/channels/{cid}/").status_code == 204
        assert not ChatOpsChannel.objects.exists()

    def test_unique_per_platform_channel(self, admin_client):
        admin_client.post("/api/chatops/channels/",
                          data={"platform": "slack", "channel_id": "C1"}, format="json")
        resp = admin_client.post("/api/chatops/channels/",
                                 data={"platform": "slack", "channel_id": "C1"}, format="json")
        assert resp.status_code == 400

    def test_viewer_cannot_create(self, viewer_client):
        resp = viewer_client.post("/api/chatops/channels/",
                                  data={"platform": "slack", "channel_id": "C9"}, format="json")
        assert resp.status_code == 403


# ── Phase 2: config API ───────────────────────────────────────────────────────

class TestConfigAPI:
    def test_defaults(self, admin_client):
        body = admin_client.get("/api/chatops/config/").json()
        assert body["allow_unmapped_read"] is True
        assert body["require_approved_channel"] is False

    def test_update_flags(self, admin_client):
        resp = admin_client.put("/api/chatops/config/",
                                data={"allow_unmapped_read": False,
                                      "require_approved_channel": True}, format="json")
        assert resp.status_code == 200
        cfg = ChatOpsConfig.load()
        assert cfg.allow_unmapped_read is False
        assert cfg.require_approved_channel is True

    def test_viewer_cannot_write(self, viewer_client):
        resp = viewer_client.put("/api/chatops/config/",
                                 data={"allow_unmapped_read": False}, format="json")
        assert resp.status_code == 403


# ── Phase 2: enforcement in the webhook path ──────────────────────────────────

class TestEnforcement:
    @pytest.fixture(autouse=True)
    def _live_slack(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("slack")

    def _post(self, api_client, user="U1", channel="C1", text="help"):
        payload = {"event": {"text": text, "user": user, "channel": channel}}
        return api_client.post("/api/webhooks/slack/", data=payload, format="json")

    def test_unmapped_read_allowed_by_default(self, api_client):
        resp = self._post(api_client)
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_unmapped_read_blocked_when_flag_off(self, api_client):
        cfg = ChatOpsConfig.load()
        cfg.allow_unmapped_read = False
        cfg.save()
        resp = self._post(api_client)
        assert resp.status_code == 200
        assert "isn't linked" in resp.json()["text"]

    def test_mapped_user_allowed_even_when_unmapped_blocked(self, api_client, admin_user):
        cfg = ChatOpsConfig.load()
        cfg.allow_unmapped_read = False
        cfg.save()
        ChatOpsIdentity.objects.create(platform="slack", platform_user_id="U1",
                                       platform_user_name="alice", user=admin_user)
        resp = self._post(api_client)
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_unapproved_channel_rejected(self, api_client):
        cfg = ChatOpsConfig.load()
        cfg.require_approved_channel = True
        cfg.save()
        resp = self._post(api_client, channel="C-random")
        assert "isn't authorized" in resp.json()["text"]

    def test_approved_channel_allows(self, api_client):
        cfg = ChatOpsConfig.load()
        cfg.require_approved_channel = True
        cfg.save()
        ChatOpsChannel.objects.create(platform="slack", channel_id="C1",
                                      purpose="both", enabled=True)
        resp = self._post(api_client, channel="C1")
        assert "spane commands" in resp.json()["text"]

    def test_notify_only_channel_rejected(self, api_client):
        cfg = ChatOpsConfig.load()
        cfg.require_approved_channel = True
        cfg.save()
        ChatOpsChannel.objects.create(platform="slack", channel_id="C1",
                                      purpose="notify", enabled=True)
        resp = self._post(api_client, channel="C1")
        assert "isn't authorized" in resp.json()["text"]

    def test_audit_row_written_on_allowed(self, api_client):
        from apps.core.models import AuditLog
        self._post(api_client)
        row = AuditLog.objects.filter(event_type=AuditLog.EventType.CHATOPS_QUERY).first()
        assert row is not None
        assert row.success is True
        assert row.metadata["platform"] == "slack"
        assert row.metadata["intent"] == "help"
        assert row.username == "unmapped"

    def test_audit_row_written_on_denied(self, api_client):
        from apps.core.models import AuditLog
        cfg = ChatOpsConfig.load()
        cfg.allow_unmapped_read = False
        cfg.save()
        self._post(api_client)
        row = AuditLog.objects.filter(event_type=AuditLog.EventType.CHATOPS_DENIED).first()
        assert row is not None
        assert row.success is False
        assert row.metadata["reason"] == "unmapped_user"

    def test_audit_records_resolved_username(self, api_client, admin_user):
        from apps.core.models import AuditLog
        ChatOpsIdentity.objects.create(platform="slack", platform_user_id="U1",
                                       user=admin_user)
        self._post(api_client)
        row = AuditLog.objects.filter(event_type=AuditLog.EventType.CHATOPS_QUERY).first()
        assert row.username == admin_user.username
        assert row.user_id == admin_user.id


# ── Phase 2: identity API ─────────────────────────────────────────────────────

class TestIdentityAPI:
    def test_admin_crud(self, admin_client, admin_user):
        resp = admin_client.post("/api/chatops/identities/",
                                 data={"platform": "slack", "platform_user_id": "U5",
                                       "platform_user_name": "bob", "user": admin_user.id},
                                 format="json")
        assert resp.status_code == 201
        assert resp.json()["username"] == admin_user.username

    def test_viewer_cannot_list_identities(self, viewer_client):
        # Identity CRUD is admin-only entirely.
        assert viewer_client.get("/api/chatops/identities/").status_code == 403

    def test_self_service_link(self, viewer_client, viewer_user):
        resp = viewer_client.post("/api/chatops/identities/link/",
                                  data={"platform": "slack", "platform_user_id": "Uself",
                                        "platform_user_name": "me"}, format="json")
        assert resp.status_code == 200
        ident = ChatOpsIdentity.objects.get(platform="slack", platform_user_id="Uself")
        assert ident.user_id == viewer_user.id

    def test_link_conflict_when_claimed_by_other(self, viewer_client, engineer_client, engineer_user):
        ChatOpsIdentity.objects.create(platform="slack", platform_user_id="Utaken",
                                       user=engineer_user)
        resp = viewer_client.post("/api/chatops/identities/link/",
                                  data={"platform": "slack", "platform_user_id": "Utaken"},
                                  format="json")
        assert resp.status_code == 409

    def test_link_requires_auth(self, api_client):
        resp = api_client.post("/api/chatops/identities/link/",
                               data={"platform": "slack", "platform_user_id": "Ux"}, format="json")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3: rich resolution, per-platform formatting, pluggable NLP fallback
# ══════════════════════════════════════════════════════════════════════════════

import json as _json
from datetime import date

from apps.chatops.format import (
    deny_response, format_discord, format_for, format_gchat, format_mattermost,
    format_slack, format_teams,
)
from apps.chatops.nlp import resolve_nlp
from apps.chatops.resolve import IntentResult, resolve


def _make_device(hostname="rtr-1", **kw):
    from apps.devices.models import Device
    return Device.objects.create(
        hostname=hostname, ip_address=kw.pop("ip", "10.0.0.5"),
        vendor=kw.pop("vendor", "Cisco"), status=kw.pop("status", "active"),
        is_reachable=kw.pop("is_reachable", True), **kw)


def _add_cve(device, severity="critical", cve_id="CVE-2024-0001", is_patched=False):
    from apps.cve.models import CVE, DeviceCVE
    cve = CVE.objects.create(cve_id=cve_id, description="test", severity=severity)
    return DeviceCVE.objects.create(device=device, cve=cve, is_patched=is_patched)


@pytest.fixture
def no_metrics(monkeypatch):
    monkeypatch.setattr("apps.devices.metrics_influx.query_device_metrics",
                        lambda did, *a, **k: {"metrics": {"cpu_pct": None, "memory_used_pct": None}})


@pytest.fixture
def with_metrics(monkeypatch):
    monkeypatch.setattr("apps.devices.metrics_influx.query_device_metrics",
                        lambda did, *a, **k: {"metrics": {"cpu_pct": 33.4, "memory_used_pct": 56.0}})


# ── resolve(): IntentResult shape per intent ──────────────────────────────────

class TestResolveDeviceStatus:
    def test_full_device_status(self, with_metrics):
        d = _make_device()
        _add_cve(d, "critical", "CVE-2024-1")
        _add_cve(d, "medium", "CVE-2024-2")
        from apps.security.models import DeviceRiskScore
        DeviceRiskScore.objects.create(device=d, score=42)
        from apps.lifecycle.models import LifecycleMilestone
        LifecycleMilestone.objects.create(device=d, milestone_type="eol",
                                          milestone_date=date(2020, 1, 1))
        res = resolve("device_status", {"name": "rtr-1"})
        assert isinstance(res, IntentResult)
        assert res.title == "rtr-1"
        f = dict(res.fields)
        assert f["Status"] == "active"
        assert f["CPU"] == "33%" and f["Memory"] == "56%"
        assert "1 critical" in f["CVEs (unpatched)"] and "1 medium" in f["CVEs (unpatched)"]
        assert f["Risk score"] == "42/100"
        assert res.severity == "critical"
        assert any("Past lifecycle" in l for l in res.lines)

    def test_no_metrics_omits_cpu_mem(self, no_metrics):
        _make_device()
        res = resolve("device_status", {"name": "rtr-1"})
        f = dict(res.fields)
        assert "CPU" not in f and "Memory" not in f
        assert f["CVEs (unpatched)"] == "none"

    def test_unreachable_is_high_severity(self, no_metrics):
        from django.utils import timezone
        _make_device(is_reachable=False, unreachable_since=timezone.now())
        res = resolve("device_status", {"name": "rtr-1"})
        f = dict(res.fields)
        assert f["Reachability"] == "unreachable"
        assert "Down since" in f
        assert res.severity == "high"

    def test_device_not_found(self, no_metrics):
        res = resolve("device_status", {"name": "ghost"})
        assert "not found" in res.title

    def test_ip_lookup_only_for_valid_ip(self, no_metrics):
        _make_device(ip="10.9.9.9", hostname="byip")
        res = resolve("device_status", {"name": "10.9.9.9"})
        assert res.title == "byip"
        # a non-IP, non-matching term must not raise (INET-safe guard)
        assert "not found" in resolve("device_status", {"name": "not-an-ip!!"}).title


class TestResolveOthers:
    def test_site_status(self):
        from apps.alerts.models import AlertEvent, AlertRule
        from apps.devices.models import Site
        from apps.security.models import DeviceRiskScore
        site = Site.objects.create(name="Dallas")
        d1 = _make_device("dal-1", status="active", ip="10.1.0.1", site=site)
        _make_device("dal-2", status="inactive", ip="10.1.0.2", site=site)
        DeviceRiskScore.objects.create(device=d1, score=77)
        rule = AlertRule.objects.create(name="x", severity="high", condition={})
        AlertEvent.objects.create(rule=rule, state="firing", labels={"device_id": str(d1.id)})
        res = resolve("site_status", {"name": "Dallas"})
        f = dict(res.fields)
        assert f["Devices active"] == "1/2"
        assert f["Firing alerts"] == "1"
        assert "77/100" in f["Worst risk"]
        assert res.severity == "high"

    def test_site_not_found(self):
        assert "not found" in resolve("site_status", {"name": "nowhere"}).title

    def test_active_alerts(self):
        from apps.alerts.models import AlertEvent, AlertRule
        r = AlertRule.objects.create(name="CPU High", severity="critical", condition={})
        AlertEvent.objects.create(rule=r, state="firing")
        res = resolve("active_alerts", {})
        assert "1 active alert" in res.title
        assert any("CPU High" in l for l in res.lines)
        assert res.severity == "critical"

    def test_active_alerts_none(self):
        res = resolve("active_alerts", {})
        assert res.title == "No active alerts."

    def test_cve_query(self):
        d = _make_device()
        _add_cve(d, "critical", "CVE-2024-A")
        _add_cve(d, "high", "CVE-2024-B")
        _add_cve(d, "high", "CVE-2024-C", is_patched=True)  # patched → excluded
        res = resolve("cve_query", {"name": "rtr-1"})
        assert "2 unpatched" in res.title
        assert any("CVE-2024-A" in l for l in res.lines)
        assert not any("CVE-2024-C" in l for l in res.lines)
        assert res.severity == "critical"

    def test_cve_query_clean(self):
        _make_device()
        res = resolve("cve_query", {"name": "rtr-1"})
        assert "no unpatched CVEs" in res.title

    def test_eol_query(self):
        d = _make_device()
        from apps.lifecycle.models import LifecycleMilestone
        LifecycleMilestone.objects.create(device=d, milestone_type="eol",
                                          milestone_date=date(2020, 1, 1))
        LifecycleMilestone.objects.create(device=d, milestone_type="eos",
                                          milestone_date=date(2099, 1, 1))
        res = resolve("eol_query", {"name": "rtr-1"})
        assert any("PASSED" in l for l in res.lines)
        assert res.severity == "medium"

    def test_help(self):
        res = resolve("help", {})
        assert res.title == "spane commands"
        assert len(res.lines) >= 4

    def test_unknown_intent_degrades_to_help(self):
        assert resolve("bogus", {}).title == "spane commands"


# ── formatters: valid platform JSON + plain fallback + no-asterisk regression ──

class TestFormatters:
    def _sample(self, with_metrics):
        d = _make_device()
        _add_cve(d, "high", "CVE-2024-Z")
        return resolve("device_status", {"name": "rtr-1"})

    def test_slack_blockkit(self, with_metrics):
        out = format_slack(self._sample(with_metrics))
        assert out["blocks"][0]["type"] == "header"
        assert isinstance(out["text"], str) and out["text"]  # plain fallback

    def test_teams_adaptive_card(self, with_metrics):
        out = format_teams(self._sample(with_metrics))
        assert out["type"] == "message"
        att = out["attachments"][0]
        assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert att["content"]["type"] == "AdaptiveCard"
        assert out["text"]  # plain fallback

    def test_gchat_cardsv2(self, with_metrics):
        out = format_gchat(self._sample(with_metrics))
        assert "cardsV2" in out and out["cardsV2"][0]["card"]["sections"]
        assert out["text"]  # plain fallback

    def test_discord_embed(self, with_metrics):
        out = format_discord(self._sample(with_metrics))
        assert out["embeds"][0]["title"]
        assert out["content"]  # plain fallback

    def test_mattermost_attachment(self, with_metrics):
        out = format_mattermost(self._sample(with_metrics))
        assert out["attachments"][0]["title"] == "rtr-1"
        assert out["text"]  # plain fallback

    def test_no_literal_asterisks_for_non_slack(self, with_metrics):
        res = self._sample(with_metrics)
        for fmt in (format_teams, format_gchat, format_discord, format_mattermost):
            blob = _json.dumps(fmt(res), ensure_ascii=False)
            assert "*" not in blob, f"{fmt.__name__} leaked a literal asterisk"

    def test_plain_fallback_is_asterisk_free(self):
        # IntentResult.plain() must never emit Slack-style bold markers.
        res = resolve("help", {})
        assert "*" not in res.plain()

    def test_format_for_dispatch_and_default(self, with_metrics):
        res = self._sample(with_metrics)
        assert "blocks" in format_for("slack", res)
        assert format_for("unknownplat", res) == {"text": res.plain()}

    def test_deny_response_shapes(self):
        assert deny_response("teams", "no")["type"] == "message"
        assert deny_response("discord", "no") == {"content": "no"}
        assert deny_response("slack", "no") == {"text": "no"}


# ── NLP fallback ──────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class TestNLP:
    def test_provider_none_returns_none(self):
        # default ChatOpsConfig has nlp_provider="none"
        assert resolve_nlp("how is rtr-1 doing") is None

    def test_local_good_json(self, monkeypatch):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "local"; cfg.nlp_endpoint = "http://ollama:11434"; cfg.save()
        captured = {}
        def fake_post(url, **kw):
            captured["url"] = url
            return _FakeResp({"response": '{"intent":"device_status","params":{"name":"rtr-1"}}'})
        monkeypatch.setattr("requests.post", fake_post)
        assert resolve_nlp("tell me about rtr-1") == ("device_status", {"name": "rtr-1"})
        assert captured["url"].endswith("/api/generate")

    def test_local_malformed_rejected(self, monkeypatch):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "local"; cfg.nlp_endpoint = "http://ollama:11434"; cfg.save()
        monkeypatch.setattr("requests.post", lambda url, **kw: _FakeResp({"response": "not json at all"}))
        assert resolve_nlp("tell me about rtr-1") is None

    def test_local_unknown_intent_rejected(self, monkeypatch):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "local"; cfg.nlp_endpoint = "http://ollama:11434"; cfg.save()
        monkeypatch.setattr("requests.post",
                            lambda url, **kw: _FakeResp({"response": '{"intent":"shutdown","params":{}}'}))
        assert resolve_nlp("nuke rtr-1") is None

    def test_local_timeout_fails_closed(self, monkeypatch):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "local"; cfg.nlp_endpoint = "http://ollama:11434"; cfg.save()
        def boom(url, **kw):
            raise RuntimeError("timed out")
        monkeypatch.setattr("requests.post", boom)
        assert resolve_nlp("tell me about rtr-1") is None

    def test_api_good_json_with_key(self, monkeypatch, fake_vault):
        from apps.chatops.models import write_chatops_secrets
        write_chatops_secrets("nlp", {"api_key": "sk-test"})
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "api"; cfg.nlp_model = "claude-haiku-4-5-20251001"; cfg.save()
        seen = {}
        def fake_post(url, **kw):
            seen["key"] = kw["headers"]["x-api-key"]
            return _FakeResp({"content": [{"type": "text",
                              "text": '{"intent":"cve_query","params":{"name":"rtr-1"}}'}]})
        monkeypatch.setattr("requests.post", fake_post)
        assert resolve_nlp("vulns on rtr-1") == ("cve_query", {"name": "rtr-1"})
        assert seen["key"] == "sk-test"

    def test_api_no_key_returns_none(self, monkeypatch, fake_vault):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "api"; cfg.save()
        # ensure requests.post is never reached without a key
        monkeypatch.setattr("requests.post",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not POST")))
        assert resolve_nlp("vulns on rtr-1") is None

    def test_config_api_key_write_only(self, admin_client, fake_vault):
        resp = admin_client.put("/api/chatops/config/",
                                data={"nlp_provider": "api", "nlp_api_key": "sk-secret"},
                                format="json")
        assert resp.status_code == 200
        assert "sk-secret" not in resp.content.decode()
        assert resp.json()["nlp_api_key_set"] is True
        assert fake_vault[chatops_vault_path("nlp")]["api_key"] == "sk-secret"


# ── NLP path still flows through enforce_policy + audit ───────────────────────

class TestNLPWebhookIntegration:
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("slack")

    def _enable_local_nlp(self, intent="device_status", name="rtr-1"):
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = "local"; cfg.nlp_endpoint = "http://ollama:11434"; cfg.save()
        payload = {"response": _json.dumps({"intent": intent, "params": {"name": name}})}
        return payload

    def test_nlp_resolves_unknown_and_audits(self, api_client, monkeypatch, with_metrics):
        _make_device("rtr-1")
        payload = self._enable_local_nlp()
        monkeypatch.setattr("requests.post", lambda url, **kw: _FakeResp(payload))
        # "tell me about rtr-1" is NOT regex-matchable → NLP kicks in
        resp = api_client.post("/api/webhooks/slack/",
                               data={"event": {"text": "tell me about rtr-1",
                                               "user": "U1", "channel": "C1"}}, format="json")
        assert resp.status_code == 200
        assert "rtr-1" in resp.json()["text"]  # device_status rendered
        from apps.core.models import AuditLog
        row = AuditLog.objects.filter(event_type=AuditLog.EventType.CHATOPS_QUERY).first()
        assert row is not None and row.metadata["intent"] == "device_status"

    def test_nlp_intent_still_enforced_when_unmapped_blocked(self, api_client, monkeypatch):
        payload = self._enable_local_nlp()
        cfg = ChatOpsConfig.load()
        cfg.allow_unmapped_read = False; cfg.save()
        monkeypatch.setattr("requests.post", lambda url, **kw: _FakeResp(payload))
        resp = api_client.post("/api/webhooks/slack/",
                               data={"event": {"text": "tell me about rtr-1",
                                               "user": "U1", "channel": "C1"}}, format="json")
        # enforce_policy must still deny the NLP-resolved intent for an unmapped user
        assert "isn't linked" in resp.json()["text"]
        from apps.core.models import AuditLog
        row = AuditLog.objects.filter(event_type=AuditLog.EventType.CHATOPS_DENIED).first()
        assert row is not None and row.metadata["intent"] == "device_status"

    def test_unknown_with_no_nlp_falls_through_to_help(self, api_client):
        resp = api_client.post("/api/webhooks/slack/",
                               data={"event": {"text": "asldkfj qwerty",
                                               "user": "U1", "channel": "C1"}}, format="json")
        assert "spane commands" in resp.json()["text"]


# ── Phase 3.5: per-platform webhook authentication ────────────────────────────
#
# Identity (user_id/channel) is read from the payload and fed to enforce_policy,
# so an unauthenticated webhook is identity-spoofable. Each platform must verify
# the request genuinely came from the platform BEFORE _classify/enforce_policy.
# The verifier is skipped only when no secret is configured (dev mode).

import base64 as _b64
import hashlib as _hashlib
import hmac as _hmac
import json as _json

from apps.chatops.models import write_chatops_secrets


def _raw_post(api_client, url, body: str, **extra):
    """POST a raw JSON body (so request.body is byte-exact for signature checks)."""
    return api_client.post(url, data=body, content_type="application/json", **extra)


class TestDiscordVerification:
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("discord")

    @staticmethod
    def _signing_key():
        from nacl.signing import SigningKey
        sk = SigningKey.generate()
        return sk, sk.verify_key.encode().hex()

    @staticmethod
    def _sign(sk, ts: str, body: str) -> str:
        return sk.sign((ts + body).encode()).signature.hex()

    def test_valid_signature_passes(self, api_client, fake_vault):
        sk, pub = self._signing_key()
        write_chatops_secrets("discord", {"public_key": pub})
        body = _json.dumps({"type": 2, "data": {"options": [{"value": "help"}]}})
        ts = "1700000000"
        resp = _raw_post(api_client, "/api/webhooks/discord/", body,
                         HTTP_X_SIGNATURE_ED25519=self._sign(sk, ts, body),
                         HTTP_X_SIGNATURE_TIMESTAMP=ts)
        assert resp.status_code == 200

    def test_ping_returns_ack(self, api_client, fake_vault):
        sk, pub = self._signing_key()
        write_chatops_secrets("discord", {"public_key": pub})
        body = _json.dumps({"type": 1})
        ts = "1700000000"
        resp = _raw_post(api_client, "/api/webhooks/discord/", body,
                         HTTP_X_SIGNATURE_ED25519=self._sign(sk, ts, body),
                         HTTP_X_SIGNATURE_TIMESTAMP=ts)
        assert resp.status_code == 200
        assert resp.json() == {"type": 1}

    def test_missing_signature_returns_401(self, api_client, fake_vault):
        _, pub = self._signing_key()
        write_chatops_secrets("discord", {"public_key": pub})
        resp = _raw_post(api_client, "/api/webhooks/discord/", _json.dumps({"type": 2}))
        assert resp.status_code == 401

    def test_wrong_signature_returns_401(self, api_client, fake_vault):
        sk, pub = self._signing_key()
        write_chatops_secrets("discord", {"public_key": pub})
        wrong, _ = self._signing_key()        # signed by a different key
        body = _json.dumps({"type": 2, "data": {"options": [{"value": "help"}]}})
        ts = "1700000000"
        resp = _raw_post(api_client, "/api/webhooks/discord/", body,
                         HTTP_X_SIGNATURE_ED25519=self._sign(wrong, ts, body),
                         HTTP_X_SIGNATURE_TIMESTAMP=ts)
        assert resp.status_code == 401

    def test_no_secret_dev_path_processes(self, api_client, fake_vault):
        # No public key stored → verification skipped; the PING still ACKs.
        resp = _raw_post(api_client, "/api/webhooks/discord/", _json.dumps({"type": 1}))
        assert resp.status_code == 200
        assert resp.json() == {"type": 1}

    def test_forged_identity_rejected_before_enforce_policy(self, api_client, fake_vault,
                                                            monkeypatch):
        """A spoofed user_id must be rejected at verification, before policy runs."""
        _, pub = self._signing_key()
        write_chatops_secrets("discord", {"public_key": pub})
        import apps.core.chatops as chatops_mod
        called = {"enforce": False}
        monkeypatch.setattr(chatops_mod, "enforce_policy",
                            lambda *a, **k: called.__setitem__("enforce", True))
        body = _json.dumps({"type": 2,
                            "member": {"user": {"id": "admin-spoof", "username": "root"}},
                            "data": {"options": [{"value": "status of core"}]}})
        resp = _raw_post(api_client, "/api/webhooks/discord/", body)   # no valid signature
        assert resp.status_code == 401
        assert called["enforce"] is False


class TestMattermostVerification:
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("mattermost")

    def _post(self, api_client, **fields):
        return api_client.post("/api/webhooks/mattermost/", data=fields, format="json")

    def test_valid_token_passes(self, api_client, fake_vault):
        write_chatops_secrets("mattermost", {"token": "s3cr3t-token"})
        resp = self._post(api_client, token="s3cr3t-token", text="help",
                          user_name="bob", channel_id="C1")
        assert resp.status_code == 200

    def test_wrong_token_returns_401(self, api_client, fake_vault):
        write_chatops_secrets("mattermost", {"token": "s3cr3t-token"})
        resp = self._post(api_client, token="nope", text="help", channel_id="C1")
        assert resp.status_code == 401

    def test_missing_token_returns_401(self, api_client, fake_vault):
        write_chatops_secrets("mattermost", {"token": "s3cr3t-token"})
        resp = self._post(api_client, text="help", channel_id="C1")
        assert resp.status_code == 401

    def test_no_secret_dev_path_processes(self, api_client, fake_vault):
        resp = self._post(api_client, text="help", user_name="bob", channel_id="C1")
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]

    def test_forged_identity_rejected_before_enforce_policy(self, api_client, fake_vault,
                                                            monkeypatch):
        write_chatops_secrets("mattermost", {"token": "s3cr3t-token"})
        import apps.core.chatops as chatops_mod
        called = {"enforce": False}
        monkeypatch.setattr(chatops_mod, "enforce_policy",
                            lambda *a, **k: called.__setitem__("enforce", True))
        resp = self._post(api_client, token="WRONG", user_id="admin-spoof",
                          user_name="root", text="status of core", channel_id="C1")
        assert resp.status_code == 401
        assert called["enforce"] is False


class TestTeamsVerification:
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("teams")

    @staticmethod
    def _key_and_b64():
        key = b"teams-outgoing-webhook-key-bytes"
        return key, _b64.b64encode(key).decode()

    @staticmethod
    def _hmac(key: bytes, body: str) -> str:
        return _b64.b64encode(_hmac.new(key, body.encode(), _hashlib.sha256).digest()).decode()

    def _body(self):
        return _json.dumps({"text": "help", "from": {"id": "u1", "name": "bob"},
                            "conversation": {"id": "C1"}})

    def test_valid_hmac_passes(self, api_client, fake_vault):
        key, b64key = self._key_and_b64()
        write_chatops_secrets("teams", {"hmac_secret": b64key})
        body = self._body()
        resp = _raw_post(api_client, "/api/webhooks/teams/", body,
                         HTTP_AUTHORIZATION=f"HMAC {self._hmac(key, body)}")
        assert resp.status_code == 200

    def test_wrong_hmac_returns_401(self, api_client, fake_vault):
        _, b64key = self._key_and_b64()
        write_chatops_secrets("teams", {"hmac_secret": b64key})
        body = self._body()
        bad = self._hmac(b"some-other-key", body)
        resp = _raw_post(api_client, "/api/webhooks/teams/", body,
                         HTTP_AUTHORIZATION=f"HMAC {bad}")
        assert resp.status_code == 401

    def test_missing_auth_returns_401(self, api_client, fake_vault):
        _, b64key = self._key_and_b64()
        write_chatops_secrets("teams", {"hmac_secret": b64key})
        resp = _raw_post(api_client, "/api/webhooks/teams/", self._body())
        assert resp.status_code == 401

    def test_no_secret_dev_path_processes(self, api_client, fake_vault):
        resp = _raw_post(api_client, "/api/webhooks/teams/", self._body())
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]


class TestGchatVerification:
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("gchat")

    def _body(self):
        return _json.dumps({"message": {"text": "help",
                                        "sender": {"displayName": "bob", "name": "users/1"}},
                            "space": {"name": "spaces/AAA"}})

    def test_valid_bearer_passes(self, api_client, fake_vault):
        write_chatops_secrets("gchat", {"bearer_token": "shared-bearer-123"})
        resp = _raw_post(api_client, "/api/webhooks/gchat/", self._body(),
                         HTTP_AUTHORIZATION="Bearer shared-bearer-123")
        assert resp.status_code == 200

    def test_wrong_bearer_returns_401(self, api_client, fake_vault):
        write_chatops_secrets("gchat", {"bearer_token": "shared-bearer-123"})
        resp = _raw_post(api_client, "/api/webhooks/gchat/", self._body(),
                         HTTP_AUTHORIZATION="Bearer nope")
        assert resp.status_code == 401

    def test_missing_auth_returns_401(self, api_client, fake_vault):
        write_chatops_secrets("gchat", {"bearer_token": "shared-bearer-123"})
        resp = _raw_post(api_client, "/api/webhooks/gchat/", self._body())
        assert resp.status_code == 401

    def test_no_secret_dev_path_processes(self, api_client, fake_vault):
        resp = _raw_post(api_client, "/api/webhooks/gchat/", self._body())
        assert resp.status_code == 200
        assert "spane commands" in resp.json()["text"]


class TestSlackVerification:
    """The pre-existing Slack HMAC step, now alongside the other four platforms."""
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        _enable("slack")

    def _body(self):
        return _json.dumps({"event": {"text": "help", "user": "U1", "channel": "C1"}})

    def test_valid_signature_passes(self, api_client, fake_vault):
        write_chatops_secrets("slack", {"signing_secret": "sign-me"})
        body = self._body()
        ts = str(int(__import__("time").time()))
        base = f"v0:{ts}:{body}"
        sig = "v0=" + _hmac.new(b"sign-me", base.encode(), _hashlib.sha256).hexdigest()
        resp = _raw_post(api_client, "/api/webhooks/slack/", body,
                         HTTP_X_SLACK_REQUEST_TIMESTAMP=ts, HTTP_X_SLACK_SIGNATURE=sig)
        assert resp.status_code == 200

    def test_wrong_signature_returns_401(self, api_client, fake_vault):
        write_chatops_secrets("slack", {"signing_secret": "sign-me"})
        body = self._body()
        ts = str(int(__import__("time").time()))
        resp = _raw_post(api_client, "/api/webhooks/slack/", body,
                         HTTP_X_SLACK_REQUEST_TIMESTAMP=ts, HTTP_X_SLACK_SIGNATURE="v0=bad")
        assert resp.status_code == 401


class TestWebhookThrottle:
    """All five webhooks carry the "chatops" ScopedRateThrottle."""
    @pytest.fixture(autouse=True)
    def _live(self, settings):
        settings.CHATOPS_ENABLED = True
        for p in ("slack", "teams", "gchat", "discord", "mattermost"):
            _enable(p)

    @pytest.mark.parametrize("platform", ["slack", "teams", "gchat", "discord", "mattermost"])
    def test_webhook_is_rate_limited(self, api_client, monkeypatch, platform):
        from django.core.cache import cache
        from rest_framework.throttling import SimpleRateThrottle
        cache.clear()
        # The "chatops" rate is disabled in test settings; patch a tiny rate.
        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"chatops": "2/min"})
        try:
            codes = [api_client.post(f"/api/webhooks/{platform}/", data={}, format="json").status_code
                     for _ in range(4)]
            assert 429 in codes, f"expected a 429 after the limit for {platform}, got {codes}"
        finally:
            cache.clear()
