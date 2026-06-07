"""
Minimal UniFi controller REST client (self-hosted / UDM "classic" API).

Used to test a controller's connection and pull its managed devices/clients for
import. Self-signed controller certs are the norm, so TLS verification defaults
off; ``trust_env=False`` stops requests' env-merge from silently overriding the
per-request ``verify`` with REQUESTS_CA_BUNDLE (see CLAUDE.md).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class UnifiError(Exception):
    """A UniFi controller request failed (login/HTTP/parse)."""


class UnifiClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 site_id: str = "default", verify_ssl: bool = False, timeout: int = 15):
        import requests

        self.base_url = f"https://{host}:{port}"
        self.site_id = site_id or "default"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        # Don't let REQUESTS_CA_BUNDLE/HTTP(S)_PROXY env override our verify= choice.
        self.session.trust_env = False
        self.session.verify = verify_ssl
        self._logged_in = False

    def login(self) -> None:
        try:
            resp = self.session.post(
                f"{self.base_url}/api/login",
                json={"username": self.username, "password": self.password},
                timeout=self.timeout, verify=self.verify_ssl,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise UnifiError(f"UniFi login failed: {exc}") from exc
        if (data.get("meta", {}) or {}).get("rc") != "ok":
            raise UnifiError(f"UniFi login rejected: {data}")
        self._logged_in = True

    def logout(self) -> None:
        try:
            self.session.post(f"{self.base_url}/api/logout", timeout=5, verify=self.verify_ssl)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _get(self, path: str, timeout: int | None = None) -> list:
        try:
            resp = self.session.get(f"{self.base_url}{path}", timeout=timeout or self.timeout,
                                    verify=self.verify_ssl)
            resp.raise_for_status()
            return resp.json().get("data", []) or []
        except Exception as exc:  # noqa: BLE001
            raise UnifiError(f"UniFi request {path} failed: {exc}") from exc

    def get_devices(self) -> list:
        """All UniFi-managed devices (APs/switches/gateways) for the site."""
        return self._get(f"/api/s/{self.site_id}/stat/device", timeout=30)

    def get_clients(self) -> list:
        """Connected client stations for the site."""
        return self._get(f"/api/s/{self.site_id}/stat/sta", timeout=30)

    def get_sites(self) -> list:
        """Sites available on the controller (each item has a 'name')."""
        return self._get("/api/self/sites")

    def __enter__(self) -> "UnifiClient":
        self.login()
        return self

    def __exit__(self, *args) -> None:
        self.logout()
