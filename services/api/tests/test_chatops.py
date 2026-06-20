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
