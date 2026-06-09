"""P2-escalation: an AUTH-FAILURE (401/403) on the UniFi / NetBox integrations
must not leak the credential into the SERVER-SIDE log either — not just the
client response.

The integration views wrap these errors and log full detail via
apps.core.errors.safe_detail (logger.error with exc_info=True). Some HTTP libs
echo the request URL (query-string auth) or a header on auth failures, which
would flip the "no credential in the exception" judgment. These tests drive the
real client error paths with a realistic 401/403 and assert the credential never
appears in str(exc) NOR in the captured server-side log (traceback included).
"""
import io
import logging
import urllib.error
import urllib.request

import pytest

from apps.core.errors import safe_detail

SENTINEL = "SENTINEL-CRED-do-not-log-7Q"
_ERR_LOGGER = "netpulse.errors"


def _server_side_log(exc, caplog):
    """Run the real server-side logging path (safe_detail) and return its output."""
    caplog.set_level(logging.DEBUG, logger=_ERR_LOGGER)
    public = safe_detail(exc, logging.getLogger(_ERR_LOGGER), "auth-failure", public="generic")
    assert public == "generic"           # client only ever sees the generic string
    return caplog.text                   # includes the exc_info traceback chain


def test_unifi_controller_login_401_no_password_in_log(caplog, monkeypatch):
    import requests
    from apps.integrations.unifi_client import UnifiClient, UnifiError

    c = UnifiClient("unifi.example.com", 443, "admin", SENTINEL)

    def _post(*a, **k):  # realistic requests 401 — URL + status, never the body
        raise requests.exceptions.HTTPError(
            "401 Client Error: Unauthorized for url: https://unifi.example.com:443/api/login")

    monkeypatch.setattr(c.session, "post", _post)
    with pytest.raises(UnifiError) as ei:
        c.login()
    assert SENTINEL not in str(ei.value)
    assert SENTINEL not in _server_side_log(ei.value, caplog)


def test_unifi_controller_login_rejected_body_no_password_in_log(caplog, monkeypatch):
    # The "login rejected" branch logs the RESPONSE body. A controller error
    # response must not round-trip the submitted password into the log.
    from apps.integrations.unifi_client import UnifiClient, UnifiError

    c = UnifiClient("unifi.example.com", 443, "admin", SENTINEL)

    class _Resp:
        def raise_for_status(self):
            return None
        def json(self):
            return {"meta": {"rc": "error", "msg": "api.err.Invalid"}}  # no creds echoed

    monkeypatch.setattr(c.session, "post", lambda *a, **k: _Resp())
    with pytest.raises(UnifiError) as ei:
        c.login()
    assert SENTINEL not in str(ei.value)
    assert SENTINEL not in _server_side_log(ei.value, caplog)


def test_unifi_cloud_401_no_api_key_in_log(caplog, monkeypatch):
    import requests
    from apps.integrations.unifi_cloud import UnifiCloudClient, UnifiCloudError

    c = UnifiCloudClient(SENTINEL)

    class _Resp:
        def raise_for_status(self):
            raise requests.exceptions.HTTPError(
                "401 Client Error: Unauthorized for url: https://api.ui.com/v1/hosts")
        def json(self):
            return {}

    monkeypatch.setattr(c.session, "get", lambda *a, **k: _Resp())
    with pytest.raises(UnifiCloudError) as ei:
        c.get_hosts()
    assert SENTINEL not in str(ei.value)
    assert SENTINEL not in _server_side_log(ei.value, caplog)


def test_netbox_403_no_token_in_log(caplog, monkeypatch):
    from apps.integrations import netbox

    c = netbox.NetBoxClient("https://nb.example.com", SENTINEL)

    def _boom(req, timeout=None):  # realistic urllib 403 — URL/code, token is in a header
        raise urllib.error.HTTPError(
            "https://nb.example.com/api/dcim/sites/", 403, "Forbidden", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(netbox.NetBoxError) as ei:
        c._get("/api/dcim/sites/")
    assert SENTINEL not in str(ei.value)
    assert SENTINEL not in _server_side_log(ei.value, caplog)
