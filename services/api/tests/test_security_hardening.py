"""
Security-hardening batch: SSRF guard, XML hardening, Jinja2 sandboxing,
setup-status info-leak, and stack-trace-exposure scrubbing in the four views.
"""
import types

import pytest

from apps.core.errors import GENERIC_ERROR
from apps.core.net_safety import UnsafeURLError, validate_outbound_url


# ── 1. SSRF: validate_outbound_url ────────────────────────────────────────────

class TestValidateOutboundUrl:
    @pytest.mark.parametrize("url", [
        "http://ollama:11434",            # local NLP target (private) — allowed
        "http://ollama:11434/api/generate",
        "https://api.anthropic.com/v1/messages",
        "http://10.10.0.5:8080/x",        # RFC-1918 is intentionally NOT blocked
    ])
    def test_accepts_http_https_including_private(self, url):
        assert validate_outbound_url(url) == url

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://evil/_",
        "dict://localhost:11211/",
        "",                                # no scheme
    ])
    def test_rejects_non_http_schemes(self, url):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url(url)

    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",   # AWS/GCP/Azure IMDS
        "http://169.254.170.2/v2/credentials",        # ECS task metadata
        "http://100.100.100.200/",                    # Alibaba
        "http://[fd00:ec2::254]/latest/",             # IPv6 IMDS
    ])
    def test_blocks_cloud_metadata(self, url):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url(url)

    def test_metadata_literal_rejected_but_scheme_only_mode_allows(self):
        # block_metadata=False (admin/internal probes) does scheme-restriction only.
        assert validate_outbound_url("http://169.254.169.254/x", block_metadata=False)
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("file://169.254.169.254/x", block_metadata=False)

    def test_blocks_hostname_resolving_to_metadata(self, monkeypatch):
        import apps.core.net_safety as ns
        # Pretend evil.example resolves to the AWS metadata IP.
        monkeypatch.setattr(ns.socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 80))])
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("http://evil.example/latest/")


# ── 1b. NLP backends fail closed on a blocked endpoint ────────────────────────

@pytest.mark.django_db
class TestNlpSsrfFailClosed:
    def _config(self, provider, endpoint):
        from apps.chatops.models import ChatOpsConfig
        cfg = ChatOpsConfig.load()
        cfg.nlp_provider = provider
        cfg.nlp_endpoint = endpoint
        cfg.save()
        return cfg

    def test_local_backend_fails_closed_without_calling_requests(self, monkeypatch):
        import apps.chatops.nlp as nlp
        self._config("local", "http://169.254.169.254")
        called = {"post": False}
        import requests
        monkeypatch.setattr(requests, "post",
                            lambda *a, **k: called.__setitem__("post", True))
        assert nlp.resolve_nlp("tell me about rtr-1") is None
        assert called["post"] is False     # rejected before any network call

    def test_api_backend_fails_closed_without_calling_requests(self, monkeypatch):
        import apps.chatops.models as models
        import apps.chatops.nlp as nlp
        self._config("api", "http://169.254.169.254/v1/messages")
        monkeypatch.setattr(models, "get_chatops_secret",
                            lambda platform, key: "fake-key" if key == "api_key" else "")
        called = {"post": False}
        import requests
        monkeypatch.setattr(requests, "post",
                            lambda *a, **k: called.__setitem__("post", True))
        assert nlp.resolve_nlp("tell me about rtr-1") is None
        assert called["post"] is False

    def test_local_backend_allows_private_ollama(self, monkeypatch):
        import apps.chatops.nlp as nlp
        self._config("local", "http://ollama:11434")
        # Capture the call instead of hitting the network; a private host is allowed.
        seen = {}

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"response": '{"intent": "help", "params": {}}'}

        def _post(url, **kw):
            seen["url"] = url
            return _Resp()

        import requests
        monkeypatch.setattr(requests, "post", _post)
        assert nlp.resolve_nlp("help me") == ("help", {})
        assert seen["url"].startswith("http://ollama:11434")


# ── 2. XML hardening: defusedxml parses nmap output unchanged ─────────────────

class TestXmlHardening:
    _NMAP = (b'<?xml version="1.0"?><nmaprun>'
             b'<host><status state="up"/><address addr="192.168.98.100" addrtype="ipv4"/></host>'
             b'<host><status state="up"/><address addr="192.168.98.152" addrtype="ipv4"/></host>'
             b'</nmaprun>')

    def test_parse_nmap_hosts_unchanged(self):
        from apps.devices.management.commands.run_discovery import parse_nmap_hosts
        assert parse_nmap_hosts(self._NMAP) == ["192.168.98.100", "192.168.98.152"]

    def test_uses_defusedxml(self):
        import apps.devices.management.commands.run_discovery as rd
        import inspect
        src = inspect.getsource(rd)
        assert "defusedxml.ElementTree" in src
        assert "import xml.etree.ElementTree" not in src

    def test_entity_expansion_is_disabled(self):
        # A billion-laughs / external-entity payload must NOT expand. defusedxml
        # raises rather than expanding; parse_nmap_hosts swallows that as "no hosts".
        from apps.devices.management.commands.run_discovery import parse_nmap_hosts
        xxe = (b'<?xml version="1.0"?>'
               b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
               b'<nmaprun><host><status state="up"/>'
               b'<address addr="&xxe;" addrtype="ipv4"/></host></nmaprun>')
        # No exception escapes, and no expanded entity leaks into the result.
        result = parse_nmap_hosts(xxe)
        assert result == [] or all("root:" not in (h or "") for h in result)


# ── 3. Compliance template sandboxing ─────────────────────────────────────────

class TestComplianceSandbox:
    def _engine(self):
        from apps.compliance.engine import ComplianceEngine
        return ComplianceEngine()

    def _template(self, content, variables=None):
        return types.SimpleNamespace(
            template_content=content, variables=variables or {}, name="t")

    def _device(self):
        return types.SimpleNamespace(
            hostname="sw1", management_ip="10.0.0.1", ip_address=None,
            platform="ios", site=None, role=None)

    def test_renders_normal_template(self):
        out = self._engine().render_template(
            self._template("hostname {{ device.hostname }}\nntp {{ ntp }}",
                           {"ntp": "1.2.3.4"}),
            self._device())
        assert out == "hostname sw1\nntp 1.2.3.4"

    def test_blocks_sandbox_escape(self):
        from jinja2.exceptions import SecurityError
        with pytest.raises(SecurityError):
            self._engine().render_template(
                self._template("{{ ''.__class__.__mro__ }}"), self._device())


# ── 4. setup_status drops the version field ───────────────────────────────────

@pytest.mark.django_db
class TestSetupStatusInfoLeak:
    def test_no_version_in_unauthenticated_response(self, api_client):
        resp = api_client.get("/api/setup/status/")
        assert resp.status_code == 200
        body = resp.json()
        assert "version" not in body
        assert {"setup_complete", "openbao_healthy", "database_healthy"} <= set(body)


# ── 5. Stack-trace exposure: the four views return generic errors ─────────────

_SENTINEL = "SECRET-PATH /srv/app/internals.py boom"


@pytest.mark.django_db
class TestNoStackTraceInViews:
    @pytest.fixture
    def device(self):
        from apps.devices.models import Device
        return Device.objects.create(hostname="sw1", ip_address="10.1.0.1",
                                     management_ip="10.1.0.1", platform="aos_cx")

    def _boom(self, *a, **k):
        raise RuntimeError(_SENTINEL)

    def test_devices_compliance_scrubs_exception(self, admin_client, device, monkeypatch):
        import apps.compliance.device_score as ds
        monkeypatch.setattr(ds, "run_and_store_compliance", self._boom)
        resp = admin_client.get(f"/api/devices/{device.pk}/compliance/")
        assert resp.status_code == 500
        assert _SENTINEL not in resp.content.decode()
        assert resp.json()["error"] == GENERIC_ERROR

    def test_compliance_run_device_scrubs_exception(self, admin_client, device, monkeypatch):
        import apps.compliance.runner as runner
        monkeypatch.setattr(runner, "run_one", self._boom)
        resp = admin_client.post(f"/api/compliance/run/{device.pk}/")
        assert resp.status_code == 500
        assert _SENTINEL not in resp.content.decode()

    def test_frameworks_list_scrubs_exception(self, admin_client, monkeypatch):
        from django.core.management import call_command
        call_command("seed_frameworks")
        import apps.frameworks.views as fv
        monkeypatch.setattr(fv, "framework_summary", self._boom)
        resp = admin_client.get("/api/frameworks/")
        assert resp.status_code == 500
        assert _SENTINEL not in resp.content.decode()
        assert resp.json()["error"] == GENERIC_ERROR

    def test_frameworks_retrieve_scrubs_exception(self, admin_client, monkeypatch):
        from django.core.management import call_command
        call_command("seed_frameworks")
        import apps.frameworks.views as fv
        monkeypatch.setattr(fv, "evaluate_framework", self._boom)
        resp = admin_client.get("/api/frameworks/sox/")
        assert resp.status_code == 500
        assert _SENTINEL not in resp.content.decode()
        assert resp.json()["error"] == GENERIC_ERROR


# ── 5b. The engine-level sources that feed those views are scrubbed at origin ──

@pytest.mark.django_db
class TestEngineSourcesScrubbed:
    def test_compliance_error_result_has_no_exception_text(self):
        from apps.compliance.engine import ComplianceEngine
        from apps.compliance.models import ComplianceTemplate
        from apps.devices.models import Device
        dev = Device.objects.create(hostname="sw2", ip_address="10.2.0.2", platform="ios")
        # Invalid Jinja syntax → render_template raises; check_device must scrub it.
        tmpl = ComplianceTemplate.objects.create(
            name="bad", template_content="{% for %}", variables={}, enabled=True)
        result = ComplianceEngine().check_device(dev, tmpl, config_text="hostname sw2")
        blob = " ".join(str(f) for f in (result.findings or []))
        assert "details in server logs" in blob          # generic message present
        assert "TemplateSyntaxError" not in blob and "block" not in blob.lower()

    def test_evidence_collector_failure_has_no_exception_text(self, monkeypatch):
        from apps.frameworks import evidence
        # Force a mapped collector to raise; the returned detail must be generic.
        key = next(iter(evidence.COLLECTORS))
        monkeypatch.setitem(evidence.COLLECTORS, key,
                            lambda ctx: (_ for _ in ()).throw(RuntimeError(_SENTINEL)))
        res = evidence.evaluate_control(key, ctx=None)
        assert _SENTINEL not in str(res)
