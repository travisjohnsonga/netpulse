"""
SonicWall SonicOS REST API client.

SonicOSX 8 disables HTTP Basic auth by default, so authentication uses RFC-7616
HTTP Digest (SHA-256). The device cert is self-signed, so TLS verification is
off by default.

IMPORTANT (verify=False gotcha): the api image sets REQUESTS_CA_BUNDLE in the
environment (for trusted outbound HTTPS). requests' env-merge turns a
per-request ``verify=None`` into that bundle path *before* the session default
applies — so ``session.verify = False`` alone is silently ignored and TLS
verification still happens. We defend against that by setting
``session.trust_env = False`` AND passing ``verify=`` explicitly on every call.

Sessions are limited on SonicWall, so always logout() (the context manager does).
"""
from __future__ import annotations

import logging

import requests
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)

# Self-signed device certs → we intentionally don't verify; silence the noise.
try:  # pragma: no cover - import guard
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


class SonicWallAuthError(Exception):
    """SonicOS rejected the credentials (auth_code != API_AUTH_SUCCESS)."""


def resolve_rest_credentials(profile, secrets: dict) -> tuple[str, str, int]:
    """
    (username, password, port) for the SonicWall REST API: prefer the HTTPS/API
    credential, fall back to SSH (SonicOS admin is usually the same account for
    both). Returns the HTTPS port from the profile, default 443.
    """
    username = (getattr(profile, "https_username", "") or "").strip()
    password = secrets.get("https_password") or ""
    port = getattr(profile, "https_port", None) or 443
    if not (username and password):
        username = username or (getattr(profile, "ssh_username", "") or "admin")
        password = password or secrets.get("ssh_password") or ""
    return (username or "admin"), password, port


class SonicWallClient:
    def __init__(self, host: str, username: str, password: str,
                 port: int = 443, verify_ssl: bool = False, timeout: int = 30):
        self.base_url = f"https://{host}:{port}/api/sonicos"
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.auth = HTTPDigestAuth(username, password)
        self.session = requests.Session()
        # Don't let REQUESTS_CA_BUNDLE / proxies from the env override verify.
        self.session.trust_env = False
        self.session.verify = verify_ssl
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._authenticated = False

    def login(self) -> dict:
        """Authenticate and return the auth info (model, privilege, …)."""
        resp = self.session.post(
            f"{self.base_url}/auth", auth=self.auth, json={"override": True},
            timeout=15, verify=self.verify_ssl,
        )
        resp.raise_for_status()
        info = (resp.json().get("status", {}).get("info") or [{}])[0]
        if info.get("auth_code") != "API_AUTH_SUCCESS":
            raise SonicWallAuthError(
                f"SonicWall auth failed: {info.get('message') or info.get('auth_code')}")
        self._authenticated = True
        return info

    def get_config(self) -> dict:
        """Full running config + system info (model, serial, firmware_version, …)."""
        resp = self.session.get(
            f"{self.base_url}/config/current", auth=self.auth,
            timeout=self.timeout, verify=self.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json()

    def logout(self) -> None:
        """Release the session (SonicWall caps concurrent sessions). Best-effort."""
        try:
            self.session.delete(
                f"{self.base_url}/auth", auth=self.auth,
                timeout=5, verify=self.verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("SonicWall logout failed (ignored): %s", exc)
        finally:
            self._authenticated = False

    def __enter__(self) -> "SonicWallClient":
        self.login()
        return self

    def __exit__(self, *args) -> None:
        self.logout()
